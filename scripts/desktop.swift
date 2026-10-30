import Cocoa

final class Launcher: NSObject, NSApplicationDelegate {
    var window: NSWindow!
    var status: NSTextField!
    var openButton: NSButton!
    var stopButton: NSButton!
    var busy = false

    func applicationDidFinishLaunching(_ notification: Notification) {
        let menu = NSMenu()
        let item = NSMenuItem()
        menu.addItem(item)
        let appMenu = NSMenu()
        appMenu.addItem(withTitle: "Закрыть окно Изbrainого", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        item.submenu = appMenu
        NSApp.mainMenu = menu
        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 500, height: 270),
                          styleMask: [.titled, .closable, .miniaturizable], backing: .buffered, defer: false)
        window.title = "Изbrainое"
        window.isReleasedWhenClosed = false
        window.center()
        let view = window.contentView!
        let title = NSTextField(labelWithString: "Твоя внешняя память")
        title.font = .systemFont(ofSize: 23, weight: .semibold)
        title.frame = NSRect(x: 30, y: 207, width: 440, height: 32)
        view.addSubview(title)
        status = NSTextField(wrappingLabelWithString: "Запускаю бота и подключаю Telegram…")
        status.frame = NSRect(x: 30, y: 135, width: 440, height: 56)
        status.font = .systemFont(ofSize: 14)
        view.addSubview(status)
        openButton = NSButton(title: "Открыть подборки", target: self, action: #selector(openApp))
        openButton.bezelStyle = .rounded
        openButton.frame = NSRect(x: 25, y: 86, width: 235, height: 36)
        view.addSubview(openButton)
        stopButton = NSButton(title: "Остановить бота", target: self, action: #selector(stopApp))
        stopButton.bezelStyle = .rounded
        stopButton.frame = NSRect(x: 270, y: 86, width: 205, height: 36)
        view.addSubview(stopButton)
        let note = NSTextField(wrappingLabelWithString: "Окно можно закрыть — бот продолжит работать.\nMac должен быть включён, подключён к интернету и не спать.")
        note.font = .systemFont(ofSize: 12)
        note.textColor = .secondaryLabelColor
        note.frame = NSRect(x: 30, y: 24, width: 440, height: 43)
        view.addSubview(note)
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
        run("start", openBrowser: true)
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }

    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        window.makeKeyAndOrderFront(nil)
        if !busy { run("start", openBrowser: true) }
        return true
    }

    @objc func openApp() { run("start", openBrowser: true) }
    @objc func stopApp() { run("stop", openBrowser: false) }

    func run(_ action: String, openBrowser: Bool) {
        if busy { return }
        busy = true
        openButton.isEnabled = false
        stopButton.isEnabled = false
        status.stringValue = action == "stop" ? "Останавливаю бота…" : "Подключаю Telegram и открываю подборки… Первый запуск может занять около минуты."
        DispatchQueue.global(qos: .userInitiated).async {
            var result: [String: Any] = [:]
            do {
                guard let resource = Bundle.main.url(forResource: "project-path", withExtension: "txt") else { throw CocoaError(.fileNoSuchFile) }
                let root = try String(contentsOf: resource, encoding: .utf8).trimmingCharacters(in: .whitespacesAndNewlines)
                let process = Process()
                process.executableURL = URL(fileURLWithPath: root + "/.venv/bin/python")
                process.arguments = [root + "/scripts/desktop.py", action]
                process.currentDirectoryURL = URL(fileURLWithPath: root)
                let pipe = Pipe()
                process.standardOutput = pipe
                process.standardError = FileHandle.nullDevice
                try process.run()
                let data = pipe.fileHandleForReading.readDataToEndOfFile()
                process.waitUntilExit()
                result = (try JSONSerialization.jsonObject(with: data)) as? [String: Any] ?? [:]
            } catch {
                result = ["error": "Не удалось открыть папку проекта. Возможно, её переместили. Верни папку на прежнее место и повтори запуск."]
            }
            let response = result
            DispatchQueue.main.async {
                self.busy = false
                self.openButton.isEnabled = true
                self.stopButton.isEnabled = response["running"] as? Bool == true
                if let error = response["error"] as? String {
                    self.status.stringValue = error
                    return
                }
                let running = response["running"] as? Bool == true
                let bot = response["bot_running"] as? Bool == true
                self.openButton.title = running ? "Открыть подборки" : "Запустить бота"
                self.status.stringValue = bot ? "Бот работает. Можно отправлять материалы в Telegram." : running ? "Приложение открыто. Telegram ещё не подключён — проверь интернет и статус в настройках." : "Бот остановлен. Материалы и настройки сохранены."
                if openBrowser, let raw = response["url"] as? String, let url = URL(string: raw) {
                    NSWorkspace.shared.open(url)
                }
            }
        }
    }
}

let app = NSApplication.shared
let delegate = Launcher()
app.delegate = delegate
app.setActivationPolicy(.regular)
app.run()
