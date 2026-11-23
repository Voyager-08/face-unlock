#define WIN32_LEAN_AND_MEAN
#define SECURITY_WIN32
#include <windows.h>
#include <ntsecapi.h>
#include <cstdio>
#include <cstring>

int main() {
    HANDLE h = nullptr;
    NTSTATUS st = LsaConnectUntrusted(&h);
    printf("LsaConnectUntrusted=0x%08lX\n", (unsigned long)st);
    if (st != 0) return 1;
    const char* names[] = {"Negotiate", "Kerberos", "MSV1_0", "NTLM", "CloudAP",
                           "MICROSOFT_AUTHENTICATION_PACKAGE_V1_0"};
    for (const char* n : names) {
        char buf[64];
        strcpy_s(buf, sizeof(buf), n);
        LSA_STRING s;
        s.Buffer = buf;
        s.Length = (USHORT)strlen(buf);
        s.MaximumLength = (USHORT)(s.Length + 1);
        ULONG id = 0xDEADBEEF;
        NTSTATUS r = LsaLookupAuthenticationPackage(h, &s, &id);
        printf("%-42s st=0x%08lX id=%lu\n", n, (unsigned long)r, (unsigned long)id);
    }
    LsaDeregisterLogonProcess(h);
    return 0;
}
