using System.Reflection;
using System.Text.Json;
using AurumPharma.Desktop.Core;
using AurumPharma_Desktop.Diagnostics;
using AurumPharma_Desktop.Services;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.Web.WebView2.Core;
using Windows.System;

namespace AurumPharma_Desktop;

public sealed partial class MainPage : Page
{
    private readonly HostSettingsService _settingsService = new();
    private HostSettings? _settings;
    private DesktopBridgeService? _bridge;
    private bool _bridgeInjected;

    public MainPage()
    {
        InitializeComponent();
        _settings = _settingsService.Load();
        _bridge = new DesktopBridgeService(_settings);
    }

    private async void Browser_CoreWebView2Initialized(
        WebView2 sender,
        CoreWebView2InitializedEventArgs args)
    {
        try
        {
            if (args.Exception is not null || _settings is null)
            {
                throw args.Exception ?? new InvalidOperationException("Host settings are unavailable.");
            }

            ConfigureBrowser(sender.CoreWebView2);
            await sender.CoreWebView2.AddScriptToExecuteOnDocumentCreatedAsync(
                CreateBridgeScript(_settings));

            if (!_bridgeInjected)
            {
                _bridgeInjected = true;
                sender.CoreWebView2.Navigate(_settings.AppUri.AbsoluteUri);
            }
        }
        catch (Exception exception)
        {
            HostLog.Error("webview_initialization_failed", exception);
            ShowError();
        }
    }

    private void ConfigureBrowser(CoreWebView2 webView)
    {
        webView.Settings.AreDevToolsEnabled = false;
        webView.Settings.AreDefaultContextMenusEnabled = false;
        webView.Settings.IsStatusBarEnabled = false;
        webView.Settings.UserAgent = $"{webView.Settings.UserAgent} AurumPharmaDesktop/{GetAppVersion()}";
        webView.NavigationStarting += WebView_NavigationStarting;
        webView.NavigationCompleted += WebView_NavigationCompleted;
        webView.NewWindowRequested += WebView_NewWindowRequested;
        webView.WebMessageReceived += WebView_WebMessageReceived;
        webView.ProcessFailed += WebView_ProcessFailed;
    }

    private void NavigateToApp()
    {
        if (_settings is null || Browser.CoreWebView2 is null)
        {
            ShowError();
            return;
        }

        ShowLoading();
        Browser.Reload();
    }

    private async void WebView_NavigationStarting(
        CoreWebView2 sender,
        CoreWebView2NavigationStartingEventArgs args)
    {
        if (_settings is not null &&
            Uri.TryCreate(args.Uri, UriKind.Absolute, out var target) &&
            (AppUriPolicy.IsSameOrigin(_settings.AppUri, target) ||
                target.OriginalString == "about:blank"))
        {
            return;
        }

        args.Cancel = true;
        HostLog.Warning("webview_external_navigation_blocked");
        await OpenInSystemBrowserAsync(args.Uri);
    }

    private void WebView_NavigationCompleted(
        CoreWebView2 sender,
        CoreWebView2NavigationCompletedEventArgs args)
    {
        if (args.IsSuccess)
        {
            if (_settings is not null &&
                Uri.TryCreate(sender.Source, UriKind.Absolute, out var source) &&
                AppUriPolicy.IsSameOrigin(_settings.AppUri, source))
            {
                HostLog.Information("webview_navigation_ready");
                LoadingPanel.Visibility = Visibility.Collapsed;
                ErrorPanel.Visibility = Visibility.Collapsed;
                Browser.Visibility = Visibility.Visible;
            }

            return;
        }

        HostLog.Warning("webview_navigation_failed");
        ShowError();
    }

    private async void WebView_NewWindowRequested(
        CoreWebView2 sender,
        CoreWebView2NewWindowRequestedEventArgs args)
    {
        args.Handled = true;
        await OpenInSystemBrowserAsync(args.Uri);
    }

    private void WebView_WebMessageReceived(
        CoreWebView2 sender,
        CoreWebView2WebMessageReceivedEventArgs args)
    {
        _bridge?.HandleMessage(args.Source, args.WebMessageAsJson);
    }

    private void WebView_ProcessFailed(CoreWebView2 sender, CoreWebView2ProcessFailedEventArgs args)
    {
        HostLog.Warning("webview_process_failed");
        ShowError();
    }

    private void RetryButton_Click(object sender, RoutedEventArgs e)
    {
        NavigateToApp();
    }

    private static async Task OpenInSystemBrowserAsync(string value)
    {
        if (!Uri.TryCreate(value, UriKind.Absolute, out var uri) ||
            (uri.Scheme != Uri.UriSchemeHttp && uri.Scheme != Uri.UriSchemeHttps))
        {
            return;
        }

        await Launcher.LaunchUriAsync(uri);
    }

    private static string CreateBridgeScript(HostSettings settings)
    {
        var origin = JsonSerializer.Serialize(settings.TrustedOrigin);
        var version = JsonSerializer.Serialize(GetAppVersion());
        var dataPlane = settings.EdgeBranchId is Guid branchId
            ? JsonSerializer.Serialize(new { mode = "edge-readonly", branchId })
            : JsonSerializer.Serialize(new { mode = "cloud" });
        return $$"""
            (() => {
              if (window.top !== window || window.location.origin !== {{origin}}) {
                return;
              }

              const bridge = Object.freeze({
                appVersion: {{version}},
                platform: "windows",
                capabilities: Object.freeze([]),
                dataPlane: Object.freeze({{dataPlane}}),
                postMessage(message) {
                  window.chrome.webview.postMessage(message);
                }
              });

              Object.defineProperty(window, "aurumDesktop", {
                configurable: false,
                enumerable: true,
                value: bridge,
                writable: false
              });
            })();
            """;
    }

    private static string GetAppVersion()
    {
        return Assembly.GetExecutingAssembly().GetName().Version?.ToString(3) ?? "0.1.0";
    }

    private void ShowLoading()
    {
        Browser.Visibility = Visibility.Collapsed;
        ErrorPanel.Visibility = Visibility.Collapsed;
        LoadingPanel.Visibility = Visibility.Visible;
    }

    private void ShowError()
    {
        Browser.Visibility = Visibility.Collapsed;
        LoadingPanel.Visibility = Visibility.Collapsed;
        ErrorPanel.Visibility = Visibility.Visible;
    }
}
