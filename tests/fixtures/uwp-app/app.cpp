// Runs on an Xbox in Dev Mode, or on Windows, as the fixture's MSIX: a
// CoreWindow app that shows an empty window until it is closed.
#include <windows.h>

#include <winrt/Windows.ApplicationModel.Core.h>
#include <winrt/Windows.UI.Core.h>

using namespace winrt;
using namespace winrt::Windows::ApplicationModel::Core;
using namespace winrt::Windows::UI::Core;

struct App : implements<App, IFrameworkViewSource, IFrameworkView> {
    IFrameworkView CreateView() { return *this; }
    void Initialize(CoreApplicationView const&) {}
    void Load(hstring const&) {}
    void Uninitialize() {}
    void SetWindow(CoreWindow const&) {}

    void Run() {
        CoreWindow window = CoreWindow::GetForCurrentThread();
        window.Activate();
        window.Dispatcher().ProcessEvents(CoreProcessEventsOption::ProcessUntilQuit);
    }
};

int __stdcall wWinMain(HINSTANCE, HINSTANCE, PWSTR, int) {
    CoreApplication::Run(make<App>());
    return 0;
}
