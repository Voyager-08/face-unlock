#include <windows.h>
#include <credentialprovider.h>
#include <cstdio>

static const CLSID CLSID_FaceUnlockCP =
    {0x3f4a8c21, 0x9d6b, 0x4e57, {0x8a, 0x2c, 0x1b, 0x5e, 0x9f, 0x7d, 0x3a, 0x64}};

int main() {
    CoInitializeEx(nullptr, COINIT_APARTMENTTHREADED);

    ICredentialProvider* p = nullptr;
    HRESULT hr = CoCreateInstance(CLSID_FaceUnlockCP, nullptr, CLSCTX_INPROC_SERVER,
                                  IID_PPV_ARGS(&p));
    printf("CoCreateInstance hr=0x%08lX\n", (unsigned long)hr);
    if (FAILED(hr)) return 1;

    hr = p->SetUsageScenario(CPUS_LOGON, 0);
    printf("SetUsageScenario hr=0x%08lX\n", (unsigned long)hr);

    DWORD count = 0, def = 0;
    BOOL autoLogon = TRUE;
    hr = p->GetCredentialCount(&count, &def, &autoLogon);
    printf("GetCredentialCount hr=0x%08lX count=%lu default=%lu autoLogon=%d\n",
           (unsigned long)hr, (unsigned long)count, (unsigned long)def, (int)autoLogon);

    DWORD fields = 0;
    p->GetFieldDescriptorCount(&fields);
    printf("fieldCount=%lu\n", (unsigned long)fields);

    if (count > 0) {
        ICredentialProviderCredential* c = nullptr;
        hr = p->GetCredentialAt(0, &c);
        printf("GetCredentialAt hr=0x%08lX\n", (unsigned long)hr);
        if (c) {
            BOOL al = FALSE;
            c->SetSelected(&al);
            LPWSTR title = nullptr;
            if (SUCCEEDED(c->GetStringValue(0, &title)) && title) {
                wprintf(L"title=%ls\n", title);
                CoTaskMemFree(title);
            }
            LPWSTR sid = nullptr;
            ICredentialProviderCredential2* c2 = nullptr;
            if (SUCCEEDED(c->QueryInterface(IID_PPV_ARGS(&c2))) && c2) {
                if (SUCCEEDED(c2->GetUserSid(&sid)) && sid) {
                    wprintf(L"userSid=%ls\n", sid);
                    CoTaskMemFree(sid);
                }
                c2->Release();
            }

            CREDENTIAL_PROVIDER_GET_SERIALIZATION_RESPONSE gsr =
                CPGSR_NO_CREDENTIAL_NOT_FINISHED;
            CREDENTIAL_PROVIDER_CREDENTIAL_SERIALIZATION cs = {};
            LPWSTR status = nullptr;
            CREDENTIAL_PROVIDER_STATUS_ICON icon = CPSI_NONE;
            hr = c->GetSerialization(&gsr, &cs, &status, &icon);
            printf("GetSerialization hr=0x%08lX gsr=%d cb=%lu authPkg=%lu\n",
                   (unsigned long)hr, (int)gsr, (unsigned long)cs.cbSerialization,
                   (unsigned long)cs.ulAuthenticationPackage);
            if (status) {
                wprintf(L"status=%ls\n", status);
                CoTaskMemFree(status);
            }
            if (cs.rgbSerialization) CoTaskMemFree(cs.rgbSerialization);
            c->Release();
        }
    }

    p->Release();
    CoUninitialize();
    return 0;
}
