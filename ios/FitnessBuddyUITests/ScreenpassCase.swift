import XCTest

/// Base class for the screenpass walk: launches the app with the deterministic launch-argument
/// contract, waits on tab-root identifiers and saves one stable-named PNG per screen into
/// `SCREENPASS_DIR` (passed via `TEST_RUNNER_SCREENPASS_DIR` by `ios/scripts/screenpass.sh`).
@MainActor
class ScreenpassCase: XCTestCase {
    private(set) var app: XCUIApplication!

    override func setUp() {
        super.setUp()
        continueAfterFailure = false
    }

    override func tearDown() {
        app?.terminate()
        app = nil
        super.tearDown()
    }

    /// Terminates any previous instance and launches a fresh, reset app in demo mode.
    /// `tab` is the `-uiTab` value: summary, workout, progress, health or coach.
    @discardableResult
    func launch(tab: String, extra: [String] = []) -> XCUIApplication {
        app?.terminate()
        let app = XCUIApplication()
        app.launchArguments = [
            "-uiReset", "YES",
            "-uiDemo", "YES",
            "-uiNoAnimations", "YES",
            "-uiFixedNow", "2026-11-04T09:41:00+01:00",
            "-uiTimeZone", "Europe/Berlin",
            "-uiTab", tab,
            "-AppleLanguages", "(en)",
            "-AppleLocale", "en_DE",
        ] + extra
        app.launch()
        self.app = app
        return app
    }

    /// Fails with a clear message unless the element with this accessibility identifier shows up.
    func waitForScreen(_ id: String, file: StaticString = #filePath, line: UInt = #line) {
        let found = app.descendants(matching: .any)[id].firstMatch.waitForExistence(timeout: 15)
        XCTAssertTrue(found, "screenpass: screen '\(id)' did not appear within 15 s", file: file, line: line)
    }

    /// Dismisses a system permission alert (e.g. notifications) if SpringBoard is showing one, so it never
    /// ends up in a screenshot. Prefers the deny button so the app state stays the same on every run.
    func dismissSystemAlerts() {
        let springboard = XCUIApplication(bundleIdentifier: "com.apple.springboard")
        let alert = springboard.alerts.firstMatch
        guard alert.waitForExistence(timeout: 1.5) else { return }
        for label in ["Don\u{2019}t Allow", "Don't Allow", "Not Now", "Allow", "OK"] where alert.buttons[label].exists {
            alert.buttons[label].tap()
            return
        }
    }

    /// Waits until the screen stops changing, then saves `<name>.png` to `SCREENPASS_DIR`.
    func snap(_ name: String, file: StaticString = #filePath, line: UInt = #line) {
        guard let dir = ProcessInfo.processInfo.environment["SCREENPASS_DIR"], !dir.isEmpty else {
            XCTFail("screenpass: SCREENPASS_DIR is not set (pass it as TEST_RUNNER_SCREENPASS_DIR to xcodebuild)",
                    file: file, line: line)
            return
        }
        dismissSystemAlerts()
        var png = XCUIScreen.main.screenshot().pngRepresentation
        var stable = false
        let deadline = Date().addingTimeInterval(6)
        while Date() < deadline {
            Thread.sleep(forTimeInterval: 0.3)
            let next = XCUIScreen.main.screenshot().pngRepresentation
            if next == png {
                stable = true
                break
            }
            png = next
        }
        if !stable { print("screenpass: unstable \(name)") }

        do {
            let folder = URL(fileURLWithPath: dir, isDirectory: true)
            try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
            try png.write(to: folder.appendingPathComponent("\(name).png"))
        } catch {
            XCTFail("screenpass: could not write \(name).png to \(dir): \(error)", file: file, line: line)
        }

        let attachment = XCTAttachment(data: png, uniformTypeIdentifier: "public.png")
        attachment.name = name
        attachment.lifetime = .deleteOnSuccess
        add(attachment)
    }
}
