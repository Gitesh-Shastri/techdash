// techdash menu bar widget.
//
// Starts the techdash server if it isn't running, shows the latest brief from
// /api/brief in the menu, and can write a fresh one by running `/brief` through
// Claude Code headless. Also the host app for the desktop widget: the widget's
// buttons open techdash://open and techdash://write, handled here, since a
// sandboxed widget can't start processes. Build with ./build.sh.

import AppKit
import WidgetKit

let home = FileManager.default.homeDirectoryForCurrentUser.path
let techdashDir = "\(home)/techdash"
let baseURL = "http://127.0.0.1:8787"
let logPath = "\(techdashDir)/cache/widget.log"
let textWidth: CGFloat = 420

struct BriefLink: Decodable { let src: String?; let url: String? }
struct BriefItem: Decodable {
    let title: String
    let why: String?
    let tag: String?
    let links: [BriefLink]?
}
struct Brief: Decodable {
    let headline: String?
    let items: [BriefItem]?
    let also: [String]?
    let built_at: Double?
}

/// Run a command through a login shell so PATH matches the terminal's
/// (python3 and claude live in ~/.local/bin and Homebrew paths).
func shell(_ command: String, onExit: ((Int32) -> Void)? = nil) -> Process {
    let process = Process()
    process.executableURL = URL(fileURLWithPath: "/bin/zsh")
    process.arguments = ["-lc", command]
    process.currentDirectoryURL = URL(fileURLWithPath: techdashDir)
    process.standardInput = FileHandle.nullDevice
    process.standardOutput = FileHandle.nullDevice
    process.standardError = FileHandle.nullDevice
    if let onExit {
        process.terminationHandler = { p in DispatchQueue.main.async { onExit(p.terminationStatus) } }
    }
    try? process.run()
    return process
}

final class AppDelegate: NSObject, NSApplicationDelegate, NSMenuDelegate {
    let statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
    let menu = NSMenu()
    var brief: Brief?
    var serverUp = false
    var writing = false
    var lastError: String?

    func applicationDidFinishLaunching(_ notification: Notification) {
        setIcon()
        menu.delegate = self
        menu.autoenablesItems = false
        statusItem.menu = menu
        rebuildMenu()
        ensureServer()
        Timer.scheduledTimer(withTimeInterval: 300, repeats: true) { [weak self] _ in self?.fetchBrief() }
    }

    /// techdash://open starts the server if needed and opens the dashboard;
    /// techdash://write writes a new brief. Both arrive from the widget.
    func application(_ application: NSApplication, open urls: [URL]) {
        for url in urls where url.scheme == "techdash" {
            switch url.host {
            case "write": writeBrief()
            default:
                ping { [weak self] up in
                    if up { self?.openDashboard() } else { self?.ensureServer(thenOpen: true) }
                }
            }
        }
    }

    func setIcon() {
        let symbol = writing ? "ellipsis.circle" : (serverUp ? "newspaper" : "newspaper.circle")
        let image = NSImage(systemSymbolName: symbol, accessibilityDescription: "techdash")
        image?.isTemplate = true
        statusItem.button?.image = image
    }

    // MARK: server

    func ensureServer(thenOpen: Bool = false) {
        ping { [weak self] up in
            guard let self else { return }
            if up { self.serverUp = true; self.fetchBrief(); return }
            _ = shell("nohup python3 techdash.py --no-open >> '\(logPath)' 2>&1 &")
            self.waitForServer(attempts: 30, thenOpen: thenOpen)
        }
    }

    func waitForServer(attempts: Int, thenOpen: Bool = false) {
        ping { [weak self] up in
            guard let self else { return }
            if up {
                self.serverUp = true
                self.fetchBrief()
                WidgetCenter.shared.reloadAllTimelines()
                if thenOpen { self.openDashboard() }
                return
            }
            guard attempts > 0 else {
                self.serverUp = false
                self.lastError = "techdash didn't start — see cache/widget.log"
                self.setIcon(); self.rebuildMenu()
                return
            }
            DispatchQueue.main.asyncAfter(deadline: .now() + 1) {
                self.waitForServer(attempts: attempts - 1, thenOpen: thenOpen)
            }
        }
    }

    func ping(_ done: @escaping (Bool) -> Void) {
        var request = URLRequest(url: URL(string: baseURL + "/")!)
        request.timeoutInterval = 2
        URLSession.shared.dataTask(with: request) { _, response, _ in
            let ok = (response as? HTTPURLResponse)?.statusCode == 200
            DispatchQueue.main.async { done(ok) }
        }.resume()
    }

    func fetchBrief() {
        var request = URLRequest(url: URL(string: baseURL + "/api/brief")!)
        request.timeoutInterval = 5
        URLSession.shared.dataTask(with: request) { [weak self] data, response, _ in
            DispatchQueue.main.async {
                guard let self else { return }
                let status = (response as? HTTPURLResponse)?.statusCode
                self.serverUp = status != nil
                if let data, status == 200, let brief = try? JSONDecoder().decode(Brief.self, from: data) {
                    self.brief = brief
                    self.lastError = nil
                } else if status == nil {
                    self.lastError = "techdash isn't running"
                }
                self.setIcon(); self.rebuildMenu()
            }
        }.resume()
    }

    // MARK: actions

    @objc func openDashboard() { NSWorkspace.shared.open(URL(string: baseURL)!) }
    @objc func reload() { serverUp ? fetchBrief() : ensureServer() }
    @objc func openLink(_ sender: NSMenuItem) {
        if let url = sender.representedObject as? URL { NSWorkspace.shared.open(url) }
    }
    @objc func openLog() { NSWorkspace.shared.open(URL(fileURLWithPath: logPath)) }

    @objc func writeBrief() {
        guard !writing else { return }
        guard serverUp else { lastError = "techdash isn't running"; ensureServer(); return }
        writing = true
        lastError = nil
        setIcon(); rebuildMenu()
        let command = "echo \"--- brief $(date)\" >> '\(logPath)'; "
            + "claude -p '/brief' --allowedTools Bash Read Write >> '\(logPath)' 2>&1"
        _ = shell(command) { [weak self] status in
            guard let self else { return }
            self.writing = false
            if status != 0 { self.lastError = "brief failed (exit \(status)) — see log" }
            self.fetchBrief()
            WidgetCenter.shared.reloadAllTimelines()
            self.notify(status == 0 ? "New brief is ready" : "Brief failed — see cache/widget.log")
        }
    }

    func notify(_ text: String) {
        _ = shell("osascript -e 'display notification \"\(text)\" with title \"techdash\"'")
    }

    // MARK: menu

    func menuWillOpen(_ menu: NSMenu) { if serverUp { fetchBrief() } }

    func textItem(_ text: String, font: NSFont, color: NSColor = .labelColor) -> NSMenuItem {
        let label = NSTextField(wrappingLabelWithString: text)
        label.font = font
        label.textColor = color
        label.preferredMaxLayoutWidth = textWidth
        let size = label.sizeThatFits(NSSize(width: textWidth, height: .greatestFiniteMagnitude))
        let container = NSView(frame: NSRect(x: 0, y: 0, width: textWidth + 28, height: size.height + 8))
        label.frame = NSRect(x: 14, y: 4, width: textWidth, height: size.height)
        container.addSubview(label)
        let item = NSMenuItem()
        item.view = container
        return item
    }

    func action(_ title: String, _ selector: Selector, key: String = "", enabled: Bool = true) -> NSMenuItem {
        let item = NSMenuItem(title: title, action: selector, keyEquivalent: key)
        item.target = self
        item.isEnabled = enabled
        return item
    }

    func rebuildMenu() {
        menu.removeAllItems()

        if let brief, let headline = brief.headline {
            menu.addItem(textItem(headline, font: .boldSystemFont(ofSize: 13)))
            if let built = brief.built_at {
                let formatter = RelativeDateTimeFormatter()
                let age = formatter.localizedString(for: Date(timeIntervalSince1970: built), relativeTo: Date())
                menu.addItem(textItem("Digest built \(age)", font: .systemFont(ofSize: 11), color: .secondaryLabelColor))
            }
            menu.addItem(.separator())

            for entry in brief.items ?? [] {
                let tag = entry.tag.map { "[\($0)] " } ?? ""
                let item = NSMenuItem(title: tag + entry.title, action: nil, keyEquivalent: "")
                let sub = NSMenu()
                if let why = entry.why { sub.addItem(textItem(why, font: .systemFont(ofSize: 12))) }
                let links = (entry.links ?? []).compactMap { link -> (String, URL)? in
                    guard let raw = link.url, let url = URL(string: raw) else { return nil }
                    return (link.src ?? url.host ?? raw, url)
                }
                if !links.isEmpty { sub.addItem(.separator()) }
                for (name, url) in links {
                    let linkItem = action("↗ \(name)", #selector(openLink(_:)))
                    linkItem.representedObject = url
                    sub.addItem(linkItem)
                }
                item.submenu = sub
                menu.addItem(item)
            }

            if let also = brief.also, !also.isEmpty {
                let item = NSMenuItem(title: "Also (\(also.count))", action: nil, keyEquivalent: "")
                let sub = NSMenu()
                for line in also { sub.addItem(textItem("• " + line, font: .systemFont(ofSize: 12))) }
                item.submenu = sub
                menu.addItem(item)
            }
        } else {
            let placeholder = serverUp ? "No brief yet — write one below." : "Starting techdash…"
            menu.addItem(textItem(placeholder, font: .systemFont(ofSize: 13), color: .secondaryLabelColor))
        }

        if let lastError {
            menu.addItem(textItem(lastError, font: .systemFont(ofSize: 11), color: .systemRed))
        }

        menu.addItem(.separator())
        menu.addItem(action(writing ? "Writing brief…" : "Write new brief", #selector(writeBrief),
                            key: "b", enabled: !writing && serverUp))
        menu.addItem(action("Open dashboard", #selector(openDashboard), key: "d", enabled: serverUp))
        menu.addItem(action(serverUp ? "Reload" : "Start techdash", #selector(reload), key: "r"))
        menu.addItem(action("Show log", #selector(openLog)))
        menu.addItem(.separator())
        menu.addItem(NSMenuItem(title: "Quit", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q"))
    }
}

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.setActivationPolicy(.accessory)
app.run()
