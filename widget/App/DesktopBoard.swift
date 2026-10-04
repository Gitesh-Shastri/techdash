// The full dashboard pinned to the desktop: a borderless window just above the
// desktop widgets (icons + 2) and far below every app window, showing the
// techdash page with all its tabs. A widget can't be full screen or host tabs;
// this can.

import AppKit
import WebKit

final class DesktopBoard: NSObject, WKNavigationDelegate, WKUIDelegate {
    static let defaultsKey = "desktopBoardVisible"

    private var window: NSWindow?
    private var webView: WKWebView?
    private let url: URL
    /// Room left for the menu bar and Dock so the board sits inside them.
    private let inset: CGFloat = 16

    init(url: URL) {
        self.url = url
        super.init()
        NotificationCenter.default.addObserver(
            self, selector: #selector(fitToScreen),
            name: NSApplication.didChangeScreenParametersNotification, object: nil)
    }

    var isVisible: Bool { window?.isVisible ?? false }

    func show() {
        if window == nil { build() }
        fitToScreen()
        window?.orderFront(nil)
        UserDefaults.standard.set(true, forKey: Self.defaultsKey)
    }

    func hide() {
        window?.orderOut(nil)
        UserDefaults.standard.set(false, forKey: Self.defaultsKey)
    }

    func toggle() { isVisible ? hide() : show() }

    /// Called after the board hides itself, so the menu can update its checkmark.
    var onHide: (() -> Void)?

    @objc private func closeClicked() {
        hide()
        onHide?()
    }

    func reload() { webView?.load(URLRequest(url: url)) }

    private func build() {
        let webView = WKWebView(frame: .zero, configuration: WKWebViewConfiguration())
        webView.navigationDelegate = self
        webView.uiDelegate = self
        webView.setValue(false, forKey: "drawsBackground")  // let the page's own background show
        webView.load(URLRequest(url: url))

        let window = NSWindow(contentRect: .zero, styleMask: [.borderless], backing: .buffered, defer: false)
        // Widgets draw at desktopIcon + 2; sit one above so they don't cover the board.
        window.level = NSWindow.Level(rawValue: Int(CGWindowLevelForKey(.desktopIconWindow)) + 3)
        window.collectionBehavior = [.canJoinAllSpaces, .stationary, .ignoresCycle, .fullScreenNone]
        window.isOpaque = false
        window.backgroundColor = .clear
        window.hasShadow = true
        window.isReleasedWhenClosed = false
        let container = NSView()
        container.wantsLayer = true
        container.layer?.cornerRadius = 18
        container.layer?.masksToBounds = true
        webView.frame = container.bounds
        webView.autoresizingMask = [.width, .height]
        container.addSubview(webView)

        // The board covers the widget (and its Full screen button), so it needs its own way out.
        let close = NSButton(image: NSImage(systemSymbolName: "xmark.circle.fill",
                                            accessibilityDescription: "Hide dashboard")!,
                             target: self, action: #selector(closeClicked))
        close.isBordered = false
        close.contentTintColor = .secondaryLabelColor
        close.toolTip = "Hide dashboard (⌘F in the menu bar brings it back)"
        close.translatesAutoresizingMaskIntoConstraints = false
        container.addSubview(close)
        NSLayoutConstraint.activate([
            close.topAnchor.constraint(equalTo: container.topAnchor, constant: 10),
            close.trailingAnchor.constraint(equalTo: container.trailingAnchor, constant: -10),
            close.widthAnchor.constraint(equalToConstant: 20),
            close.heightAnchor.constraint(equalToConstant: 20),
        ])
        window.contentView = container

        self.webView = webView
        self.window = window
    }

    @objc private func fitToScreen() {
        guard let window, let screen = NSScreen.main else { return }
        window.setFrame(screen.visibleFrame.insetBy(dx: inset, dy: inset), display: true)
    }

    // Stories open in the browser; only the dashboard itself loads in the board.
    func webView(_ webView: WKWebView, decidePolicyFor action: WKNavigationAction,
                 decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        if let target = action.request.url, target.host != url.host, action.navigationType == .linkActivated {
            NSWorkspace.shared.open(target)
            return decisionHandler(.cancel)
        }
        decisionHandler(.allow)
    }

    func webView(_ webView: WKWebView, createWebViewWith configuration: WKWebViewConfiguration,
                 for action: WKNavigationAction, windowFeatures: WKWindowFeatures) -> WKWebView? {
        if let target = action.request.url { NSWorkspace.shared.open(target) }
        return nil
    }

    // The server may not be up yet on login; retry until it is.
    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
        DispatchQueue.main.asyncAfter(deadline: .now() + 3) { [weak self] in self?.reload() }
    }
}
