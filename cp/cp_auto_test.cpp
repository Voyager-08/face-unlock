#include <windows.h>
#include <credentialprovider.h>
#include <ntsecapi.h>
#include <cstdio>
#include <string>

static const CLSID CLSID_FaceUnlockCP =
    {0x3f4a8c21, 0x9d6b, 0x4e57, {0x8a, 0x2c, 0x1b, 0x5e, 0x9f, 0x7d, 0x3a, 0x64}};

class Events : public ICredentialProviderEvents {
public:
    Events() {
        m_ev = CreateEventW(nullptr, TRUE, FALSE, nullptr);
        CoCreateFreeThreadedMarshaler(this, &m_ftm);
    }
    ~Events() {
        if (m_ftm) m_ftm->Release();
        if (m_ev) CloseHandle(m_ev);
    }

    HRESULT STDMETHODCALLTYPE QueryInterface(REFIID riid, void** ppv) override {
        if (!ppv) return E_POINTER;
        if (riid == IID_IUnknown || riid == IID_ICredentialProviderEvents) {
            *ppv = static_cast<ICredentialProviderEvents*>(this);
            AddRef();
            return S_OK;
        }
        if (riid == IID_IMarshal && m_ftm) return m_ftm->QueryInterface(riid, ppv);
        *ppv = nullptr;
        return E_NOINTERFACE;
    }
    ULONG STDMETHODCALLTYPE AddRef() override { return InterlockedIncrement(&m_ref); }
    ULONG STDMETHODCALLTYPE Release() override {
        long r = InterlockedDecrement(&m_ref);
        if (r == 0) delete this;
        return r;
    }
    HRESULT STDMETHODCALLTYPE CredentialsChanged(UINT_PTR ctx) override {
        printf("  [event] CredentialsChanged ctx=%llu\n", (unsigned long long)ctx);
        SetEvent(m_ev);
        return S_OK;
    }

    bool Wait(DWORD ms) { return WaitForSingleObject(m_ev, ms) == WAIT_OBJECT_0; }
    void Reset() { ResetEvent(m_ev); }

private:
    LONG m_ref = 1;
    HANDLE m_ev = nullptr;
    IUnknown* m_ftm = nullptr;
};

static void PrintField(ICredentialProviderCredential* c, DWORD id, const char* label) {
    LPWSTR s = nullptr;
    if (SUCCEEDED(c->GetStringValue(id, &s)) && s) {
        printf("%s=%ls\n", label, s);
        CoTaskMemFree(s);
    }
}

int main() {
    CoInitializeEx(nullptr, COINIT_APARTMENTTHREADED);

    Events* ev = new Events();
    ICredentialProvider* p = nullptr;
    HRESULT hr = CoCreateInstance(CLSID_FaceUnlockCP, nullptr, CLSCTX_INPROC_SERVER,
                                  IID_PPV_ARGS(&p));
    if (FAILED(hr)) { printf("CoCreateInstance 0x%08lX\n", (unsigned long)hr); return 1; }

    p->SetUsageScenario(CPUS_LOGON, 0);
    p->Advise(ev, 777);

    DWORD count = 0, def = 0;
    BOOL autoLogon = FALSE;
    p->GetCredentialCount(&count, &def, &autoLogon);
    printf("initial: count=%lu auto=%d\n", (unsigned long)count, (int)autoLogon);

    ICredentialProviderCredential* c = nullptr;
    p->GetCredentialAt(0, &c);
    BOOL al = FALSE;
    c->SetSelected(&al);
    printf("SetSelected auto=%d\n", (int)al);

    CREDENTIAL_PROVIDER_GET_SERIALIZATION_RESPONSE gsr = CPGSR_NO_CREDENTIAL_NOT_FINISHED;
    CREDENTIAL_PROVIDER_CREDENTIAL_SERIALIZATION cs = {};
    LPWSTR status = nullptr;
    CREDENTIAL_PROVIDER_STATUS_ICON icon = CPSI_NONE;
    hr = c->GetSerialization(&gsr, &cs, &status, &icon);
    printf("first GetSerialization hr=0x%08lX gsr=%d (expect 0)\n", (unsigned long)hr, (int)gsr);
    if (status) CoTaskMemFree(status);

    printf("waiting for first CredentialsChanged...\n");
    if (!ev->Wait(9000)) { printf("TIMEOUT waiting for status event\n"); return 1; }
    ev->Reset();
    PrintField(c, 0, "  title");
    PrintField(c, 1, "  status");

    printf("waiting for auto-submit...\n");
    Sleep(2200);
    ev->Reset();

    p->GetCredentialCount(&count, &def, &autoLogon);
    printf("after success: count=%lu auto=%d\n", (unsigned long)count, (int)autoLogon);

    BOOL al2 = FALSE;
    c->SetSelected(&al2);
    gsr = CPGSR_NO_CREDENTIAL_NOT_FINISHED;
    cs = {};
    status = nullptr;
    hr = c->GetSerialization(&gsr, &cs, &status, &icon);
    printf("second GetSerialization hr=0x%08lX gsr=%d cb=%lu\n", (unsigned long)hr, (int)gsr,
           (unsigned long)cs.cbSerialization);

    if (gsr == CPGSR_RETURN_CREDENTIAL_FINISHED &&
        cs.cbSerialization >= sizeof(KERB_INTERACTIVE_UNLOCK_LOGON)) {
        auto* k = (KERB_INTERACTIVE_UNLOCK_LOGON*)cs.rgbSerialization;
        auto unpack = [&](const UNICODE_STRING& us) -> std::wstring {
            if (us.Length == 0) return L"";
            if ((ULONG_PTR)us.Buffer + us.Length > cs.cbSerialization) return L"<BAD-OFFSET>";
            const wchar_t* p = (const wchar_t*)((const BYTE*)cs.rgbSerialization + (ULONG_PTR)us.Buffer);
            return std::wstring(p, us.Length / sizeof(wchar_t));
        };
        std::wstring d = unpack(k->Logon.LogonDomainName);
        std::wstring u = unpack(k->Logon.UserName);
        std::wstring pw = unpack(k->Logon.Password);
        printf("blob: msgType=%d domain=%ls user=%ls passLen=%zu\n",
               (int)k->Logon.MessageType, d.c_str(), u.c_str(), pw.size());
    }

    if (status) CoTaskMemFree(status);
    if (cs.rgbSerialization) CoTaskMemFree(cs.rgbSerialization);

    c->Release();
    p->UnAdvise();
    p->Release();
    ev->Release();
    CoUninitialize();
    return (gsr == CPGSR_RETURN_CREDENTIAL_FINISHED && cs.cbSerialization > 0) ? 0 : 1;
}
