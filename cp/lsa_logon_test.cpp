#define WIN32_LEAN_AND_MEAN
#define SECURITY_WIN32
#include <windows.h>
#include <ntsecapi.h>
#include <dpapi.h>
#include <wincrypt.h>
#include <cstdio>
#include <cstring>
#include <fstream>
#include <string>
#include <vector>

static std::string ReadAll(const wchar_t* path) {
    std::ifstream f(path, std::ios::binary);
    if (!f) return "";
    return std::string((std::istreambuf_iterator<char>(f)), std::istreambuf_iterator<char>());
}

static std::string JsonStr(const std::string& json, const char* key) {
    std::string needle = std::string("\"") + key + "\"";
    size_t p = json.find(needle);
    if (p == std::string::npos) return "";
    p = json.find(':', p + needle.size());
    if (p == std::string::npos) return "";
    p = json.find('"', p);
    if (p == std::string::npos) return "";
    std::string out;
    for (++p; p < json.size(); ++p) {
        if (json[p] == '\\' && p + 1 < json.size()) { out += json[++p]; }
        else if (json[p] == '"') break;
        else out += json[p];
    }
    return out;
}

static std::wstring Wide(const std::string& s) {
    if (s.empty()) return L"";
    int n = MultiByteToWideChar(CP_UTF8, 0, s.data(), (int)s.size(), nullptr, 0);
    std::wstring w(n, L'\0');
    MultiByteToWideChar(CP_UTF8, 0, s.data(), (int)s.size(), &w[0], n);
    return w;
}

static bool B64(const std::string& in, std::vector<BYTE>& out) {
    DWORD n = 0;
    if (!CryptStringToBinaryA(in.c_str(), 0, CRYPT_STRING_BASE64, nullptr, &n, nullptr, nullptr)) return false;
    out.resize(n);
    return CryptStringToBinaryA(in.c_str(), 0, CRYPT_STRING_BASE64, out.data(), &n, nullptr, nullptr) != FALSE;
}

static bool Unprotect(const std::vector<BYTE>& in, std::vector<BYTE>& out) {
    DATA_BLOB din{(DWORD)in.size(), const_cast<BYTE*>(in.data())};
    DATA_BLOB dout{};
    if (!CryptUnprotectData(&din, nullptr, nullptr, nullptr, nullptr, CRYPTPROTECT_LOCAL_MACHINE, &dout))
        return false;
    out.assign(dout.pbData, dout.pbData + dout.cbData);
    LocalFree(dout.pbData);
    return true;
}

static ULONG PackageId(HANDLE lsa, const char* name) {    LSA_STRING s;
    char buf[64];
    strcpy_s(buf, sizeof(buf), name);
    s.Buffer = buf;
    s.Length = (USHORT)strlen(buf);
    s.MaximumLength = (USHORT)(s.Length + 1);
    ULONG id = 0xFFFFFFFF;
    NTSTATUS st = LsaLookupAuthenticationPackage(lsa, &s, &id);
    if (st != 0) return 0xFFFFFFFF;
    return id;
}

static bool EnablePrivilege(LPCWSTR name) {
    HANDLE token = nullptr;
    if (!OpenProcessToken(GetCurrentProcess(), TOKEN_ADJUST_PRIVILEGES | TOKEN_QUERY, &token))
        return false;
    LUID luid{};
    if (!LookupPrivilegeValueW(nullptr, name, &luid)) { CloseHandle(token); return false; }
    TOKEN_PRIVILEGES tp{};
    tp.PrivilegeCount = 1;
    tp.Privileges[0].Luid = luid;
    tp.Privileges[0].Attributes = SE_PRIVILEGE_ENABLED;
    SetLastError(ERROR_SUCCESS);
    BOOL ok = AdjustTokenPrivileges(token, FALSE, &tp, sizeof(tp), nullptr, nullptr);
    DWORD err = GetLastError();
    CloseHandle(token);
    printf("EnablePrivilege(%ls) ok=%d err=%lu\n", name, ok ? 1 : 0, (unsigned long)err);
    return ok && err == ERROR_SUCCESS;
}

static void TryLogon(HANDLE lsa, const char* pkgName, ULONG logonType, ULONG msgType,
                     const std::wstring& domain, const std::wstring& user,
                     const std::wstring& pass) {
    ULONG pkg = PackageId(lsa, pkgName);
    DWORD cch = (DWORD)(domain.size() + user.size() + pass.size() + 3);
    DWORD header = (msgType == 7) ? sizeof(KERB_INTERACTIVE_UNLOCK_LOGON)
                                  : sizeof(KERB_INTERACTIVE_LOGON);
    DWORD cb = header + cch * sizeof(wchar_t);
    std::vector<BYTE> buf(cb, 0);
    auto* k = (KERB_INTERACTIVE_LOGON*)buf.data();
    k->MessageType = (KERB_LOGON_SUBMIT_TYPE)msgType;
    wchar_t* p = (wchar_t*)(buf.data() + header);
    auto fill = [&](UNICODE_STRING& us, const std::wstring& s) {
        us.Length = (USHORT)(s.size() * sizeof(wchar_t));
        us.MaximumLength = (USHORT)((s.size() + 1) * sizeof(wchar_t));
        us.Buffer = p;
        memcpy(p, s.c_str(), (s.size() + 1) * sizeof(wchar_t));
        p += s.size() + 1;
    };
    fill(k->LogonDomainName, domain);
    fill(k->UserName, user);
    fill(k->Password, pass);

    TOKEN_SOURCE src;
    memcpy(src.SourceName, "FaceTest", 8);
    AllocateLocallyUniqueId(&src.SourceIdentifier);
    LSA_STRING origin;
    char on[] = "FaceTest";
    origin.Buffer = on;
    origin.Length = (USHORT)strlen(on);
    origin.MaximumLength = (USHORT)(origin.Length + 1);

    LUID logonId{};
    HANDLE token = nullptr;
    QUOTA_LIMITS quotas{};
    NTSTATUS sub = 0;
    PVOID profile = nullptr;
    ULONG profileLen = 0;

    NTSTATUS st = LsaLogonUser(lsa, &origin, (SECURITY_LOGON_TYPE)logonType, pkg,
                               buf.data(), cb, nullptr, &src, &profile, &profileLen,
                               &logonId, &token, &quotas, &sub);
    printf("pkg=%-10s(id=%lu) logonType=%lu msgType=%lu -> st=0x%08lX sub=0x%08lX\n",
           pkgName, (unsigned long)pkg, (unsigned long)logonType, (unsigned long)msgType,
           (unsigned long)st, (unsigned long)sub);
    if (token) CloseHandle(token);
    if (profile) LsaFreeReturnBuffer(profile);
}

int main() {
    printf("=== LSA logon test ===\n");
    EnablePrivilege(SE_TCB_NAME);
    std::string json = ReadAll(L"D:\\software-file\\opencode\\face-unlock\\data\\credential.json");
    if (json.empty()) { printf("no config\n"); return 1; }
    std::wstring user = Wide(JsonStr(json, "username"));
    std::wstring domain = Wide(JsonStr(json, "domain"));
    std::vector<BYTE> blob, plain;
    if (!B64(JsonStr(json, "password_dpapi"), blob) || !Unprotect(blob, plain)) {
        printf("dpapi failed\n");
        return 1;
    }
    std::wstring pass((const wchar_t*)plain.data(), plain.size() / sizeof(wchar_t));
    printf("user=%ls domain=%ls passLen=%zu\n", user.c_str(), domain.c_str(), pass.size());

    HANDLE lsa = nullptr;
    LSA_STRING procName;
    char pn[] = "FaceTest";
    procName.Buffer = pn;
    procName.Length = (USHORT)strlen(pn);
    procName.MaximumLength = (USHORT)(procName.Length + 1);
    NTSTATUS st = LsaRegisterLogonProcess(&procName, &lsa, nullptr);
    if (st != 0) {
        printf("LsaRegisterLogonProcess failed 0x%08lX (run as SYSTEM)\n", (unsigned long)st);
        return 1;
    }

    const ULONG KERB_INTERACTIVE = 2;
    const ULONG KERB_UNLOCK = 7;
    const ULONG LOGON_INTERACTIVE = 2;
    const ULONG LOGON_UNLOCK = 7;

    TryLogon(lsa, "Negotiate", LOGON_INTERACTIVE, KERB_INTERACTIVE, domain, user, pass);
    TryLogon(lsa, "Negotiate", LOGON_UNLOCK, KERB_UNLOCK, domain, user, pass);
    TryLogon(lsa, "CloudAP", LOGON_INTERACTIVE, KERB_INTERACTIVE, domain, user, pass);
    TryLogon(lsa, "CloudAP", LOGON_UNLOCK, KERB_UNLOCK, domain, user, pass);
    TryLogon(lsa, "CloudAP", LOGON_INTERACTIVE, KERB_UNLOCK, domain, user, pass);

    LsaDeregisterLogonProcess(lsa);
    return 0;
}
