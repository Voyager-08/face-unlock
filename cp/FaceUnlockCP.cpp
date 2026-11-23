// Face Unlock Credential Provider
// Adds a "face unlock" credential to the Windows sign-in screen. Recognition is
// delegated to the user-session Python agent over a named pipe. On success the
// credential is submitted automatically (no clicks), after a brief "识别成功".

#define WIN32_LEAN_AND_MEAN
#define SECURITY_WIN32
#include <windows.h>
#include <credentialprovider.h>
#include <ntsecapi.h>
#include <dpapi.h>
#include <wincrypt.h>
#include <objidl.h>
#include <shlwapi.h>
#include <wrl.h>
#include <wrl/implements.h>

#include <atomic>
#include <chrono>
#include <cmath>
#include <condition_variable>
#include <cstdarg>
#include <cstdio>
#include <cstring>
#include <fstream>
#include <mutex>
#include <string>
#include <thread>
#include <vector>

using namespace Microsoft::WRL;

// {3F4A8C21-9D6B-4E57-8A2C-1B5E9F7D3A64}
static const CLSID CLSID_FaceUnlockCP =
    {0x3f4a8c21, 0x9d6b, 0x4e57, {0x8a, 0x2c, 0x1b, 0x5e, 0x9f, 0x7d, 0x3a, 0x64}};

static const wchar_t* kDefaultConfigPath =
    L"D:\\Program Files (x86)\\FaceUnlock\\data\\credential.json";
static const wchar_t* kDefaultPipe = L"\\\\.\\pipe\\faceunlock";
static const wchar_t* kLogPath =
    L"D:\\Program Files (x86)\\FaceUnlock\\logs\\cp.log";

static std::wstring g_configPath = kDefaultConfigPath;
static std::wstring g_pipe = kDefaultPipe;
static std::wstring g_logPath = kLogPath;
static std::string g_authPackage = "Negotiate";
static std::string g_msgType = "auto";

struct Config {
    std::wstring username;
    std::wstring domain;
    std::wstring sid;
    std::wstring password;
    bool loaded = false;
};

static Config g_config;
static std::once_flag g_configOnce;
static std::mutex g_logMutex;

static IGlobalInterfaceTable* g_git = nullptr;
static DWORD g_gitCookie = 0;
static UINT_PTR g_adviseContext = 0;
static std::mutex g_evtMutex;

static std::atomic<bool> g_autoStarted{false};
static std::atomic<bool> g_resultReady{false};
static std::atomic<bool> g_resultOk{false};
static std::atomic<bool> g_serialized{false};
static CREDENTIAL_PROVIDER_USAGE_SCENARIO g_usage = CPUS_LOGON;

// ---------------------------------------------------------------- utilities

static void Log(const wchar_t* fmt, ...) {
    wchar_t buf[1024];
    va_list ap;
    va_start(ap, fmt);
    _vsnwprintf_s(buf, _TRUNCATE, fmt, ap);
    va_end(ap);

    SYSTEMTIME st;
    GetLocalTime(&st);
    std::lock_guard<std::mutex> lock(g_logMutex);
    FILE* f = nullptr;
    if (_wfopen_s(&f, g_logPath.c_str(), L"a, ccs=UTF-8") == 0 && f) {
        fwprintf(f, L"[%02d:%02d:%02d] %ls\n", st.wHour, st.wMinute, st.wSecond, buf);
        fclose(f);
    }
}

static std::wstring Utf8ToWide(const std::string& s) {
    if (s.empty()) return L"";
    int n = MultiByteToWideChar(CP_UTF8, 0, s.data(), (int)s.size(), nullptr, 0);
    std::wstring w((size_t)n, L'\0');
    MultiByteToWideChar(CP_UTF8, 0, s.data(), (int)s.size(), &w[0], n);
    return w;
}

static std::string ReadFileUtf8(const std::wstring& path) {
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
        char c = json[p];
        if (c == '\\' && p + 1 < json.size()) {
            char n = json[++p];
            switch (n) {
                case 'n': out += '\n'; break;
                case 't': out += '\t'; break;
                case 'r': out += '\r'; break;
                default: out += n; break;
            }
        } else if (c == '"') {
            break;
        } else {
            out += c;
        }
    }
    return out;
}

static bool Base64Decode(const std::string& in, std::vector<BYTE>& out) {
    DWORD n = 0;
    if (!CryptStringToBinaryA(in.c_str(), 0, CRYPT_STRING_BASE64, nullptr, &n, nullptr, nullptr))
        return false;
    out.resize(n);
    return CryptStringToBinaryA(in.c_str(), 0, CRYPT_STRING_BASE64, out.data(), &n, nullptr, nullptr) != FALSE;
}

static bool DpapiDecryptMachine(const std::vector<BYTE>& in, std::vector<BYTE>& out) {
    DATA_BLOB din{(DWORD)in.size(), const_cast<BYTE*>(in.data())};
    DATA_BLOB dout{};
    if (!CryptUnprotectData(&din, nullptr, nullptr, nullptr, nullptr, CRYPTPROTECT_LOCAL_MACHINE, &dout))
        return false;
    out.assign(dout.pbData, dout.pbData + dout.cbData);
    LocalFree(dout.pbData);
    return true;
}

static void LoadConfig() {
    HKEY hk = nullptr;
    if (RegOpenKeyExW(HKEY_LOCAL_MACHINE, L"SOFTWARE\\FaceUnlockCP", 0, KEY_READ, &hk) == ERROR_SUCCESS) {
        wchar_t buf[1024];
        DWORD cb = sizeof(buf), type = 0;
        if (RegQueryValueExW(hk, L"ConfigPath", nullptr, &type, (BYTE*)buf, &cb) == ERROR_SUCCESS &&
            type == REG_SZ && buf[0]) {
            g_configPath = buf;
        }
        cb = sizeof(buf);
        type = 0;
        if (RegQueryValueExW(hk, L"PipeName", nullptr, &type, (BYTE*)buf, &cb) == ERROR_SUCCESS &&
            type == REG_SZ && buf[0]) {
            g_pipe = buf;
        }
        cb = sizeof(buf);
        type = 0;
        if (RegQueryValueExW(hk, L"LogPath", nullptr, &type, (BYTE*)buf, &cb) == ERROR_SUCCESS &&
            type == REG_SZ && buf[0]) {
            g_logPath = buf;
        }
        cb = sizeof(buf);
        type = 0;
        if (RegQueryValueExW(hk, L"AuthPackage", nullptr, &type, (BYTE*)buf, &cb) == ERROR_SUCCESS &&
            type == REG_SZ && buf[0]) {
            char narrow[64] = {};
            WideCharToMultiByte(CP_ACP, 0, buf, -1, narrow, sizeof(narrow) - 1, nullptr, nullptr);
            g_authPackage = narrow;
        }
        cb = sizeof(buf);
        type = 0;
        if (RegQueryValueExW(hk, L"MessageType", nullptr, &type, (BYTE*)buf, &cb) == ERROR_SUCCESS &&
            type == REG_SZ && buf[0]) {
            char narrow[64] = {};
            WideCharToMultiByte(CP_ACP, 0, buf, -1, narrow, sizeof(narrow) - 1, nullptr, nullptr);
            g_msgType = narrow;
        }
        RegCloseKey(hk);
    }

    std::string json = ReadFileUtf8(g_configPath);
    if (json.empty()) {
        Log(L"config not readable: %ls", g_configPath.c_str());
        return;
    }
    g_config.domain = Utf8ToWide(JsonStr(json, "domain"));
    g_config.username = Utf8ToWide(JsonStr(json, "username"));
    g_config.sid = Utf8ToWide(JsonStr(json, "sid"));

    std::vector<BYTE> blob, plain;
    std::string b64 = JsonStr(json, "password_dpapi");
    if (!b64.empty() && Base64Decode(b64, blob) && DpapiDecryptMachine(blob, plain) &&
        plain.size() >= sizeof(wchar_t)) {
        g_config.password.assign((const wchar_t*)plain.data(), plain.size() / sizeof(wchar_t));
        g_config.loaded = !g_config.username.empty();
    }
    Log(L"config loaded=%d user=%ls domain=%ls", g_config.loaded ? 1 : 0,
        g_config.username.c_str(), g_config.domain.c_str());
}

static bool IsEnabled() {
    DWORD v = 1, cb = sizeof(v), type = 0;
    HKEY hk = nullptr;
    if (RegOpenKeyExW(HKEY_LOCAL_MACHINE, L"SOFTWARE\\FaceUnlockCP", 0, KEY_READ, &hk) == ERROR_SUCCESS) {
        RegQueryValueExW(hk, L"Enabled", nullptr, &type, (BYTE*)&v, &cb);
        RegCloseKey(hk);
    }
    return v != 0;
}

static void EnsureConfig() { std::call_once(g_configOnce, LoadConfig); }

static ULONG GetAuthPackage() {
    static ULONG pkg = 0;
    static std::once_flag once;
    std::call_once(once, [] {
        HANDLE h = nullptr;
        NTSTATUS st = LsaConnectUntrusted(&h);
        Log(L"LsaConnectUntrusted st=0x%08lX", (unsigned long)st);
        if (st == 0) {
            LSA_STRING name;
            char n[64];
            strcpy_s(n, sizeof(n), g_authPackage.c_str());
            name.Buffer = n;
            name.Length = (USHORT)strlen(n);
            name.MaximumLength = (USHORT)(strlen(n) + 1);
            ULONG id = 0;
            NTSTATUS st2 = LsaLookupAuthenticationPackage(h, &name, &id);
            Log(L"LsaLookupAuthenticationPackage st=0x%08lX id=%lu", (unsigned long)st2,
                (unsigned long)id);
            if (st2 == 0) pkg = id;
            LsaDeregisterLogonProcess(h);
        }
    });
    return pkg;
}

static bool PipeCall(const std::string& req, DWORD timeoutMs, std::string& out,
                     DWORD connectBudgetMs = 1500, bool verbose = true) {
    HANDLE h = INVALID_HANDLE_VALUE;
    ULONGLONG start = GetTickCount64();
    while (true) {
        h = CreateFileW(g_pipe.c_str(), GENERIC_READ | GENERIC_WRITE, 0, nullptr, OPEN_EXISTING,
                        0, nullptr);
        if (h != INVALID_HANDLE_VALUE) break;
        DWORD err = GetLastError();
        if (err != ERROR_PIPE_BUSY && err != ERROR_FILE_NOT_FOUND) {
            if (verbose) Log(L"pipe open failed err=%lu", (unsigned long)err);
            return false;
        }
        if (GetTickCount64() - start > connectBudgetMs) {
            if (verbose) Log(L"pipe not available err=%lu", (unsigned long)err);
            return false;
        }
        WaitNamedPipeW(g_pipe.c_str(), 50);
        Sleep(20);
    }

    DWORD mode = PIPE_READMODE_MESSAGE;
    SetNamedPipeHandleState(h, &mode, nullptr, nullptr);

    DWORD written = 0;
    if (!WriteFile(h, req.data(), (DWORD)req.size(), &written, nullptr)) {
        if (verbose) Log(L"pipe write failed err=%lu", (unsigned long)GetLastError());
        CloseHandle(h);
        return false;
    }

    std::mutex m;
    std::condition_variable cv;
    bool done = false;
    bool ok = false;
    std::string resp;
    std::thread reader([&] {
        char buf[65536];
        DWORD rd = 0;
        BOOL r = ReadFile(h, buf, sizeof(buf) - 1, &rd, nullptr);
        std::lock_guard<std::mutex> lk(m);
        if (r && rd > 0) {
            resp.assign(buf, rd);
            ok = true;
        }
        done = true;
        cv.notify_all();
    });

    bool got = false;
    {
        std::unique_lock<std::mutex> lk(m);
        got = cv.wait_for(lk, std::chrono::milliseconds(timeoutMs), [&] { return done; });
    }
    if (!got) {
        if (verbose) Log(L"pipe read timeout");
        CancelSynchronousIo(reader.native_handle());
        CancelIoEx(h, nullptr);
        CloseHandle(h);
        h = INVALID_HANDLE_VALUE;
        reader.join();
        return false;
    }
    reader.join();
    if (h != INVALID_HANDLE_VALUE) CloseHandle(h);
    out = resp;
    return ok;
}

static bool ResponseOk(const std::string& s) {
    size_t p = s.find("\"ok\"");
    if (p == std::string::npos) return false;
    p = s.find(':', p);
    if (p == std::string::npos) return false;
    p = s.find_first_not_of(" \t\r\n", p + 1);
    return p != std::string::npos && s.compare(p, 4, "true") == 0;
}

static std::wstring ReasonText(const std::string& resp) {
    std::string r = JsonStr(resp, "reason");
    std::string hint = JsonStr(resp, "hint");
    if (r == "too_dark") return L"光线太暗，请开灯后重试";
    if (r == "no_face") return L"未检测到人脸，请正对屏幕";
    if (r == "bad_pose") {
        if (hint == "too_far") return L"请靠近一点";
        if (hint == "too_close") return L"请离远一点";
        return L"请正对屏幕，保持面部完整";
    }
    if (r == "no_match") return L"未识别到本人，请重试或使用密码";
    if (r == "camera") return L"摄像头不可用";
    return L"未识别到本人，请重试或使用密码";
}

static void NotifyChanged() {
    std::lock_guard<std::mutex> lk(g_evtMutex);
    if (!g_git || !g_gitCookie) {
        Log(L"NotifyChanged: no events registered (git=%d cookie=%lu)", g_git ? 1 : 0,
            (unsigned long)g_gitCookie);
        return;
    }
    ICredentialProviderEvents* ev = nullptr;
    HRESULT hr = g_git->GetInterfaceFromGlobal(g_gitCookie, IID_PPV_ARGS(&ev));
    if (SUCCEEDED(hr) && ev) {
        ev->CredentialsChanged(g_adviseContext);
        ev->Release();
        Log(L"NotifyChanged: ok");
    } else {
        Log(L"NotifyChanged: GetInterfaceFromGlobal hr=0x%08lX", (unsigned long)hr);
    }
}

static bool AgentAvailable() {
    EnsureConfig();
    if (!g_config.loaded) return false;
    std::string resp;
    if (!PipeCall("{\"cmd\":\"ping\"}", 400, resp, 250, false)) return false;
    return ResponseOk(resp);
}

static HRESULT PackSerialization(CREDENTIAL_PROVIDER_CREDENTIAL_SERIALIZATION* pcpcs) {
    EnsureConfig();
    const std::wstring& domain = g_config.domain;
    const std::wstring& user = g_config.username;
    const std::wstring& pass = g_config.password;

    bool unlock;
    if (g_msgType == "unlock") unlock = true;
    else if (g_msgType == "interactive") unlock = false;
    else unlock = (g_usage == CPUS_UNLOCK_WORKSTATION);

    // Packed format expected by LSA: UNICODE_STRING.Buffer is a byte offset from
    // the start of this buffer, and strings are not null terminated.
    DWORD cb = (DWORD)(sizeof(KERB_INTERACTIVE_UNLOCK_LOGON) +
                       (domain.size() + user.size() + pass.size()) * sizeof(wchar_t));
    BYTE* buf = (BYTE*)CoTaskMemAlloc(cb);
    if (!buf) return E_OUTOFMEMORY;
    ZeroMemory(buf, cb);

    auto* k = (KERB_INTERACTIVE_UNLOCK_LOGON*)buf;
    k->Logon.MessageType = unlock ? KerbWorkstationUnlockLogon : KerbInteractiveLogon;
    k->LogonId.LowPart = 0;
    k->LogonId.HighPart = 0;

    BYTE* cursor = buf + sizeof(KERB_INTERACTIVE_UNLOCK_LOGON);
    auto pack = [&](UNICODE_STRING& us, const std::wstring& s) {
        us.Length = (USHORT)(s.size() * sizeof(wchar_t));
        us.MaximumLength = us.Length;
        us.Buffer = (PWSTR)(cursor - buf);
        if (!s.empty()) memcpy(cursor, s.data(), us.Length);
        cursor += us.Length;
    };
    pack(k->Logon.LogonDomainName, domain);
    pack(k->Logon.UserName, user);
    pack(k->Logon.Password, pass);

    pcpcs->clsidCredentialProvider = CLSID_FaceUnlockCP;
    pcpcs->rgbSerialization = buf;
    pcpcs->cbSerialization = cb;
    pcpcs->ulAuthenticationPackage = GetAuthPackage();
    return S_OK;
}

// ---------------------------------------------------------------- tile image

static HBITMAP GetFaceBitmap() {
    static HBITMAP cached = nullptr;
    if (cached) return cached;

    const int S = 64;
    BITMAPINFO bi{};
    bi.bmiHeader.biSize = sizeof(BITMAPINFOHEADER);
    bi.bmiHeader.biWidth = S;
    bi.bmiHeader.biHeight = -S;
    bi.bmiHeader.biPlanes = 1;
    bi.bmiHeader.biBitCount = 32;
    bi.bmiHeader.biCompression = BI_RGB;

    void* bits = nullptr;
    HBITMAP bmp = CreateDIBSection(nullptr, &bi, DIB_RGB_COLORS, &bits, nullptr, 0);
    if (!bmp || !bits) return nullptr;
    memset(bits, 0, (size_t)S * S * 4);

    auto* px = (uint32_t*)bits;
    auto put = [&](int x, int y, uint8_t r, uint8_t g, uint8_t b, uint8_t a) {
        if (x < 0 || y < 0 || x >= S || y >= S) return;
        uint8_t* q = (uint8_t*)&px[y * S + x];
        q[0] = b;
        q[1] = g;
        q[2] = r;
        q[3] = a;
    };

    const double cx = 31.5, cy = 31.5;
    for (int y = 0; y < S; y++) {
        for (int x = 0; x < S; x++) {
            double dx = x - cx, dy = y - cy;
            double d = std::sqrt(dx * dx + dy * dy);
            if (d >= 25.0 && d <= 27.5) put(x, y, 255, 255, 255, 255);
        }
    }
    for (int y = 22; y <= 30; y++) {
        for (int x = 17; x <= 48; x++) {
            double d1 = std::hypot(x - 23.5, y - 26.0);
            double d2 = std::hypot(x - 40.5, y - 26.0);
            if (d1 <= 2.6 || d2 <= 2.6) put(x, y, 255, 255, 255, 255);
        }
    }
    for (double deg = 35.0; deg <= 145.0; deg += 0.4) {
        double rad = deg * 3.14159265358979 / 180.0;
        int x = (int)std::lround(32.0 + 13.5 * std::cos(rad));
        int y = (int)std::lround(33.0 + 13.5 * std::sin(rad));
        put(x, y, 255, 255, 255, 255);
        put(x + 1, y, 255, 255, 255, 255);
    }

    cached = bmp;
    return cached;
}

// ---------------------------------------------------------------- credential

enum FieldId { FIELD_STATUS = 0, FIELD_COUNT = 1 };

struct FieldDef {
    CREDENTIAL_PROVIDER_FIELD_TYPE type;
    const wchar_t* label;
};

static const FieldDef kFields[FIELD_COUNT] = {
    {CPFT_LARGE_TEXT, L"人脸解锁"},
};

class FaceCredential : public RuntimeClass<RuntimeClassFlags<ClassicCom>, ICredentialProviderCredential2> {
public:
    FaceCredential() = default;

    IFACEMETHODIMP SetSelected(BOOL* pbAutoLogon) override {
        if (pbAutoLogon) *pbAutoLogon = TRUE;
        Log(L"SetSelected pendingResult=%d", (g_resultReady && g_resultOk) ? 1 : 0);
        if (!(g_resultReady && g_resultOk)) {
            g_serialized = false;
            std::lock_guard<std::mutex> lk(m_mutex);
            StartAttemptLocked();
        }
        return S_OK;
    }

    IFACEMETHODIMP SetDeselected() override { return S_OK; }

    IFACEMETHODIMP Advise(ICredentialProviderCredentialEvents* pcpce) override {
        m_credEvents = pcpce;
        return S_OK;
    }

    IFACEMETHODIMP UnAdvise() override {
        m_credEvents.Reset();
        return S_OK;
    }

    IFACEMETHODIMP GetFieldState(DWORD dwFieldID, CREDENTIAL_PROVIDER_FIELD_STATE* pcpfs,
                                 CREDENTIAL_PROVIDER_FIELD_INTERACTIVE_STATE* pcpfis) override {
        if (!pcpfs || !pcpfis) return E_INVALIDARG;
        if (dwFieldID != FIELD_STATUS) return E_INVALIDARG;
        *pcpfs = CPFS_HIDDEN;
        *pcpfis = CPFIS_NONE;
        return S_OK;
    }

    IFACEMETHODIMP GetStringValue(DWORD dwFieldID, LPWSTR* ppwsz) override {
        if (!ppwsz || dwFieldID != FIELD_STATUS) return E_INVALIDARG;
        std::lock_guard<std::mutex> lk(m_mutex);
        return SHStrDupW(m_status.c_str(), ppwsz);
    }

    IFACEMETHODIMP GetBitmapValue(DWORD, HBITMAP*) override { return E_NOTIMPL; }

    IFACEMETHODIMP GetCheckboxValue(DWORD, BOOL*, LPWSTR*) override {
        return E_NOTIMPL;
    }
    IFACEMETHODIMP GetSubmitButtonValue(DWORD, DWORD*) override {
        return E_NOTIMPL;
    }
    IFACEMETHODIMP GetComboBoxValueCount(DWORD, DWORD*, DWORD*) override { return E_NOTIMPL; }
    IFACEMETHODIMP GetComboBoxValueAt(DWORD, DWORD, LPWSTR*) override { return E_NOTIMPL; }
    IFACEMETHODIMP SetStringValue(DWORD, LPCWSTR) override { return E_NOTIMPL; }
    IFACEMETHODIMP SetCheckboxValue(DWORD, BOOL) override { return E_NOTIMPL; }
    IFACEMETHODIMP SetComboBoxSelectedValue(DWORD, DWORD) override { return E_NOTIMPL; }
    IFACEMETHODIMP CommandLinkClicked(DWORD) override { return E_NOTIMPL; }

    IFACEMETHODIMP GetSerialization(CREDENTIAL_PROVIDER_GET_SERIALIZATION_RESPONSE* pcpgsr,
                                    CREDENTIAL_PROVIDER_CREDENTIAL_SERIALIZATION* pcpcs,
                                    LPWSTR* ppwszOptionalStatusText,
                                    CREDENTIAL_PROVIDER_STATUS_ICON* pcpsiOptionalStatusIcon) override {
        if (!pcpgsr || !pcpcs) return E_INVALIDARG;
        *pcpgsr = CPGSR_NO_CREDENTIAL_NOT_FINISHED;
        if (ppwszOptionalStatusText) *ppwszOptionalStatusText = nullptr;
        if (pcpsiOptionalStatusIcon) *pcpsiOptionalStatusIcon = CPSI_NONE;

        if (g_resultReady && g_resultOk) {
            HRESULT hr = PackSerialization(pcpcs);
            if (SUCCEEDED(hr)) {
                g_serialized = true;
                g_resultReady = false;
                {
                    std::lock_guard<std::mutex> lk(m_mutex);
                    m_status = L"正在登录…";
                }
                Log(L"GetSerialization: credential submitted");
                *pcpgsr = CPGSR_RETURN_CREDENTIAL_FINISHED;
                return S_OK;
            }
            Log(L"serialization failed hr=0x%08lX", (unsigned long)hr);
        }

        EnsureConfig();
        if (!g_config.loaded) {
            std::lock_guard<std::mutex> lk(m_mutex);
            m_status = L"人脸解锁尚未配置";
            if (ppwszOptionalStatusText) SHStrDupW(m_status.c_str(), ppwszOptionalStatusText);
            if (pcpsiOptionalStatusIcon) *pcpsiOptionalStatusIcon = CPSI_ERROR;
            return S_OK;
        }

        {
            std::lock_guard<std::mutex> lk(m_mutex);
            StartAttemptLocked();
        }
        if (ppwszOptionalStatusText) {
            std::lock_guard<std::mutex> lk(m_mutex);
            SHStrDupW(m_status.c_str(), ppwszOptionalStatusText);
        }
        return S_OK;
    }

    IFACEMETHODIMP ReportResult(NTSTATUS, NTSTATUS, LPWSTR* ppwszOptionalStatusText,
                                CREDENTIAL_PROVIDER_STATUS_ICON* pcpsiOptionalStatusIcon) override {
        if (ppwszOptionalStatusText) *ppwszOptionalStatusText = nullptr;
        if (pcpsiOptionalStatusIcon) *pcpsiOptionalStatusIcon = CPSI_NONE;
        return S_OK;
    }

    IFACEMETHODIMP GetUserSid(LPWSTR* ppwszSid) override {
        if (!ppwszSid) return E_INVALIDARG;
        EnsureConfig();
        if (g_config.sid.empty()) {
            *ppwszSid = nullptr;
            return S_FALSE;
        }
        return SHStrDupW(g_config.sid.c_str(), ppwszSid);
    }

    void BeginBackgroundAttempt() { StartAttempt(); }

private:
    void StartAttempt() {
        std::lock_guard<std::mutex> lk(m_mutex);
        StartAttemptLocked();
    }

    void StartAttemptLocked() {
        if (m_running) return;
        if (g_resultReady && g_resultOk) return;
        m_running = true;
        m_status = L"正在人脸识别…";
        g_autoStarted = true;
        g_resultReady = false;
        g_resultOk = false;
        std::thread([this] { Worker(); }).detach();
    }

    void Worker() {
        CoInitializeEx(nullptr, COINIT_MULTITHREADED);
        bool armed = false;
        for (int attempt = 0; attempt < 30 && !g_serialized && !armed; ++attempt) {
            std::string resp;
            bool callOk = PipeCall("{\"cmd\":\"recognize\",\"timeout\":3}", 12000, resp);
            bool ok = callOk && ResponseOk(resp);
            Log(L"recognition attempt=%d ok=%d reason=%hs", attempt, ok ? 1 : 0,
                JsonStr(resp, "reason").c_str());
            if (ok) {
                g_resultOk = true;
                g_resultReady = true;
                NotifyChanged();
                Log(L"auto-submit armed");
                armed = true;
                break;
            }
            for (int i = 0; i < 150 && !g_serialized; ++i) {
                std::this_thread::sleep_for(std::chrono::milliseconds(100));
            }
        }
        {
            std::lock_guard<std::mutex> lk(m_mutex);
            m_running = false;
        }
        if (!armed) {
            g_resultOk = false;
            g_resultReady = true;
            Log(L"background recognition finished without match");
        }
        CoUninitialize();
    }

    std::mutex m_mutex;
    ComPtr<ICredentialProviderCredentialEvents> m_credEvents;
    bool m_running = false;
    std::wstring m_status = L"正对摄像头即可自动解锁";
};

// ---------------------------------------------------------------- provider

class FaceProvider : public RuntimeClass<RuntimeClassFlags<ClassicCom>, ICredentialProvider> {
public:
    IFACEMETHODIMP SetUsageScenario(CREDENTIAL_PROVIDER_USAGE_SCENARIO cpus, DWORD) override {
        m_usage = cpus;
        g_usage = cpus;
        Log(L"SetUsageScenario %d", (int)cpus);
        return S_OK;
    }

    IFACEMETHODIMP SetSerialization(const CREDENTIAL_PROVIDER_CREDENTIAL_SERIALIZATION*) override {
        return S_FALSE;
    }

    IFACEMETHODIMP Advise(ICredentialProviderEvents* pcpe, UINT_PTR upAdviseContext) override {
        std::lock_guard<std::mutex> lk(g_evtMutex);
        HRESULT hr = S_OK;
        if (!g_git) {
            hr = CoCreateInstance(CLSID_StdGlobalInterfaceTable, nullptr, CLSCTX_INPROC_SERVER,
                                  IID_PPV_ARGS(&g_git));
            Log(L"Advise: create GIT hr=0x%08lX", (unsigned long)hr);
        }
        if (g_git && pcpe) {
            if (g_gitCookie) {
                g_git->RevokeInterfaceFromGlobal(g_gitCookie);
                g_gitCookie = 0;
            }
            hr = g_git->RegisterInterfaceInGlobal(pcpe, IID_ICredentialProviderEvents,
                                                  &g_gitCookie);
            Log(L"Advise: register hr=0x%08lX cookie=%lu ctx=%llu", (unsigned long)hr,
                (unsigned long)g_gitCookie, (unsigned long long)upAdviseContext);
            if (SUCCEEDED(hr)) g_adviseContext = upAdviseContext;
        }
        return S_OK;
    }

    IFACEMETHODIMP UnAdvise() override {
        std::lock_guard<std::mutex> lk(g_evtMutex);
        if (g_git && g_gitCookie) {
            g_git->RevokeInterfaceFromGlobal(g_gitCookie);
            g_gitCookie = 0;
        }
        return S_OK;
    }

    IFACEMETHODIMP GetFieldDescriptorCount(DWORD* pdwCount) override {
        if (!pdwCount) return E_INVALIDARG;
        *pdwCount = FIELD_COUNT;
        return S_OK;
    }

    IFACEMETHODIMP GetFieldDescriptorAt(DWORD dwIndex,
                                        CREDENTIAL_PROVIDER_FIELD_DESCRIPTOR** ppcpfd) override {
        if (!ppcpfd || dwIndex >= FIELD_COUNT) return E_INVALIDARG;
        auto* d = (CREDENTIAL_PROVIDER_FIELD_DESCRIPTOR*)CoTaskMemAlloc(
            sizeof(CREDENTIAL_PROVIDER_FIELD_DESCRIPTOR));
        if (!d) return E_OUTOFMEMORY;
        ZeroMemory(d, sizeof(*d));
        d->dwFieldID = dwIndex;
        d->cpft = kFields[dwIndex].type;
        d->guidFieldType = GUID_NULL;
        if (FAILED(SHStrDupW(kFields[dwIndex].label, &d->pszLabel))) {
            CoTaskMemFree(d);
            return E_OUTOFMEMORY;
        }
        *ppcpfd = d;
        return S_OK;
    }

    IFACEMETHODIMP GetCredentialCount(DWORD* pdwCount, DWORD* pdwDefault,
                                      BOOL* pbAutoLogonWithDefault) override {
        if (!pdwCount || !pdwDefault || !pbAutoLogonWithDefault) return E_INVALIDARG;
        if (!IsEnabled()) {
            *pdwCount = 0;
            *pdwDefault = CREDENTIAL_PROVIDER_NO_DEFAULT;
            *pbAutoLogonWithDefault = FALSE;
            return S_OK;
        }
        EnsureConfig();
        bool scenarioOk = (m_usage == CPUS_LOGON || m_usage == CPUS_UNLOCK_WORKSTATION);
        bool agent = scenarioOk && g_config.loaded && (g_autoStarted || AgentAvailable());
        if (!scenarioOk || !g_config.loaded || !agent) {
            Log(L"GetCredentialCount usage=%d loaded=%d agent=%d -> 0", (int)m_usage,
                g_config.loaded ? 1 : 0, agent ? 1 : 0);
            *pdwCount = 0;
            *pdwDefault = CREDENTIAL_PROVIDER_NO_DEFAULT;
            *pbAutoLogonWithDefault = FALSE;
            return S_OK;
        }

        if (!m_cred) {
            m_cred = Make<FaceCredential>();
            if (!m_cred) return E_OUTOFMEMORY;
        }
        if (!g_autoStarted && !g_serialized) {
            m_cred->BeginBackgroundAttempt();
        }

        bool ready = !g_serialized && g_resultReady && g_resultOk;
        Log(L"GetCredentialCount usage=%d agent=%d started=%d ready=%d ok=%d serialized=%d expose=%d",
            (int)m_usage, agent ? 1 : 0, g_autoStarted ? 1 : 0, g_resultReady ? 1 : 0,
            g_resultOk ? 1 : 0, g_serialized ? 1 : 0, ready ? 1 : 0);

        *pdwCount = ready ? 1 : 0;
        *pdwDefault = ready ? 0 : CREDENTIAL_PROVIDER_NO_DEFAULT;
        *pbAutoLogonWithDefault = ready ? TRUE : FALSE;
        return S_OK;
    }

    IFACEMETHODIMP GetCredentialAt(DWORD dwIndex,
                                   ICredentialProviderCredential** ppcpc) override {
        if (!ppcpc || dwIndex != 0) return E_INVALIDARG;
        if (!m_cred) {
            m_cred = Make<FaceCredential>();
            if (!m_cred) return E_OUTOFMEMORY;
        }
        *ppcpc = static_cast<ICredentialProviderCredential*>(m_cred.Get());
        (*ppcpc)->AddRef();
        return S_OK;
    }

private:
    CREDENTIAL_PROVIDER_USAGE_SCENARIO m_usage = CPUS_LOGON;
    ComPtr<FaceCredential> m_cred;
};

class FaceClassFactory : public RuntimeClass<RuntimeClassFlags<ClassicCom>, IClassFactory> {
public:
    IFACEMETHODIMP CreateInstance(IUnknown* pUnkOuter, REFIID riid, void** ppv) override {
        if (pUnkOuter) return CLASS_E_NOAGGREGATION;
        if (!ppv) return E_INVALIDARG;
        ComPtr<FaceProvider> p = Make<FaceProvider>();
        if (!p) return E_OUTOFMEMORY;
        return p->QueryInterface(riid, ppv);
    }
    IFACEMETHODIMP LockServer(BOOL) override { return S_OK; }
};

// ---------------------------------------------------------------- exports

STDAPI DllGetClassObject(REFCLSID rclsid, REFIID riid, void** ppv) {
    if (!ppv) return E_INVALIDARG;
    if (!IsEqualCLSID(rclsid, CLSID_FaceUnlockCP)) return CLASS_E_CLASSNOTAVAILABLE;
    ComPtr<FaceClassFactory> f = Make<FaceClassFactory>();
    if (!f) return E_OUTOFMEMORY;
    return f->QueryInterface(riid, ppv);
}

STDAPI DllCanUnloadNow() { return S_FALSE; }

BOOL APIENTRY DllMain(HMODULE, DWORD reason, LPVOID) {
    if (reason == DLL_PROCESS_ATTACH) DisableThreadLibraryCalls(GetModuleHandleW(nullptr));
    return TRUE;
}
