import Darwin
import SwiftUI
import UIKit
import XCTest
@testable import FitnessBuddy

/// The fast tier of the screenpass walk (`make ios-screens`): every screen of `SnapshotRegistry` rendered inside the
/// hosted app, no app relaunch per screen. For each screen it writes the launch values into `UserDefaults.standard`,
/// builds a fresh reset + demo app state (`UITestSupport.makeSnapshotEnvironment`), mounts the app's root view in a
/// full-screen window of the host's scene, waits until the screen's accessibility identifier shows up and the picture
/// stops changing, and saves the composited screen as `<name>.png` into `SCREENPASS_DIR` (passed as
/// `TEST_RUNNER_SCREENPASS_DIR`), framed like the XCUITest shots. `SCREENPASS_ONLY=coach,health` keeps only those tabs.
@MainActor
final class SnapshotScreens: XCTestCase {
    /// Where the last screen's time went (printed with each shot).
    private var phases: [(String, Date)] = []
    private func mark(_ name: String) { phases.append((name, Date())) }
    /// The values every `-uiReset -uiDemo` screenpass launch passes (ScreenpassCase.launch), without the dash.
    private static let launchValues: [String: String] = [
        "uiReset": "YES",
        "uiDemo": "YES",
        "uiNoAnimations": "YES",
        "uiFixedNow": "2026-11-04T09:41:00+01:00",
        "uiTimeZone": "Europe/Berlin",
    ]
    /// Every key a registry entry may set; all are cleared before and after each screen.
    private static let launchKeys: Set<String> = Set(launchValues.keys).union(
        ["uiTab", "uiSheet", "uiRoute", "uiWorkout", "uiSegment", "uiLift", "uiRun"])

    /// Seconds a screen must be on screen before it can count as stable (`SCREENPASS_MIN_AGE` overrides).
    private static let minAge = ProcessInfo.processInfo.environment["SCREENPASS_MIN_AGE"].flatMap(Double.init) ?? 1.0

    override func setUp() {
        super.setUp()
        continueAfterFailure = true
    }

    func testScreens() async throws {
        let env = ProcessInfo.processInfo.environment
        let dir = try XCTUnwrap(env["SCREENPASS_DIR"].flatMap { $0.isEmpty ? nil : $0 },
                                "screenpass: SCREENPASS_DIR is not set (pass it as TEST_RUNNER_SCREENPASS_DIR to xcodebuild)")
        let folder = URL(fileURLWithPath: dir, isDirectory: true)
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)

        let only = Set((env["SCREENPASS_ONLY"] ?? "").split(separator: ",")
            .map { $0.trimmingCharacters(in: .whitespaces) }.filter { !$0.isEmpty })
        let screens = SnapshotRegistry.all.filter { only.isEmpty || only.contains($0.tab) }
        XCTAssertEqual(Set(SnapshotRegistry.all.map(\.name)).count, SnapshotRegistry.all.count, "screenpass: duplicate screen names")
        XCTAssertFalse(screens.isEmpty, "screenpass: no screens for SCREENPASS_ONLY=\(only.sorted().joined(separator: ","))")

        Self.enableAccessibility()
        let scene = try XCTUnwrap(UIApplication.shared.connectedScenes.compactMap { $0 as? UIWindowScene }.first,
                                  "screenpass: the host app has no window scene")
        // The host app's own (empty, unit-test mode) window stays out of the way while the pass runs.
        let hostWindows = scene.windows.filter { !$0.isHidden }
        hostWindows.forEach { $0.isHidden = true }
        defer { hostWindows.forEach { $0.isHidden = false } }

        let started = Date()
        for screen in screens {
            let t0 = Date()
            if let png = await render(screen, in: scene) {
                do {
                    try png.write(to: folder.appendingPathComponent("\(screen.name).png"))
                } catch {
                    XCTFail("screenpass: could not write \(screen.name).png to \(dir): \(error)")
                }
            }
            var previous = t0
            let split = phases.map { name, at in
                defer { previous = at }
                return String(format: "%@ %.2f", name, at.timeIntervalSince(previous))
            }.joined(separator: ", ")
            phases = []
            print(String(format: "screenpass: shot %@ %.2fs (%@)", screen.name, Date().timeIntervalSince(t0), split))
        }
        print(String(format: "screenpass: %d screens in %.1fs", screens.count, Date().timeIntervalSince(started)))
    }

    // MARK: One screen

    /// Mounts a fresh app for `screen`, waits until it is ready and stable, returns its PNG (nil after a failure).
    /// A sheet that starts presented (`@State` set from a deep link in `init`) is occasionally dropped by UIKit when
    /// SwiftUI asks for it before the tab bar controller's view is in the window ("Attempt to present ... whose view is
    /// not in the window hierarchy"); such a screen is mounted again, at most `attempts` times in all.
    private func render(_ screen: SnapshotScreen, in scene: UIWindowScene, attempts: Int = 3) async -> Data? {
        let defaults = UserDefaults.standard
        defer { Self.launchKeys.forEach { defaults.removeObject(forKey: $0) } }
        for attempt in 1...attempts {
            Self.launchKeys.forEach { defaults.removeObject(forKey: $0) }
            for (key, value) in Self.launchValues.merging(screen.args, uniquingKeysWith: { _, new in new }) {
                defaults.set(value, forKey: key)
            }
            defaults.set(screen.tab, forKey: "uiTab")

            let env = UITestSupport.makeSnapshotEnvironment()
            mark("env")
            let window = UIWindow(windowScene: scene)
            window.frame = scene.coordinateSpace.bounds
            window.rootViewController = UIHostingController(rootView: RootView().appRoot(env))
            window.makeKeyAndVisible()
            let mountedAt = Date()
            window.layoutIfNeeded()
            // No accessibility queries until the first frames are on screen: they make SwiftUI update the hierarchy
            // early, which can ask for a launch sheet before its presenter is in the window.
            await pump(0.15)

            let last = attempt == attempts
            let ready = await waitForIdentifier(screen.ready, in: window, timeout: last ? 10 : 5)
            mark("ready")
            var png: Data?
            if ready {
                png = await stableCapture(screen, of: window, mountedAt: mountedAt, minAge: Self.minAge)
                mark("stable")
            } else if last {
                saveFailure(screen, of: window)
                XCTFail("screenpass: screen '\(screen.ready)' did not appear for \(screen.name) after \(attempts) mounts "
                        + "(identifiers seen: \(Self.identifiers(in: window).prefix(25).joined(separator: ", ")))")
            } else {
                print("screenpass: retry \(screen.name) ('\(screen.ready)' not showing after mount \(attempt))")
            }
            env.restTimer.cancel()
            await tearDown(window)
            mark("teardown")
            if ready || last { return png }
        }
        return nil
    }

    /// Dismisses what the screen presented and waits until UIKit has finished, so the next screen's presentations
    /// never meet a transition still in progress, then removes the window.
    private func tearDown(_ window: UIWindow) async {
        if let root = window.rootViewController, root.presentedViewController != nil {
            root.dismiss(animated: false)
            let until = Date().addingTimeInterval(3)
            while root.presentedViewController != nil, Date() < until { await pump(0.02) }
        }
        window.isHidden = true
        window.rootViewController = nil
        await pump(0.02)
    }

    /// Ready: the screen's own identifier is in the window's accessibility tree.
    private func waitForIdentifier(_ id: String, in window: UIWindow, timeout: Double) async -> Bool {
        let deadline = Date().addingTimeInterval(timeout)
        while !Self.contains(id, in: window) {
            guard Date() < deadline else { return false }
            await pump(0.02)
        }
        return true
    }

    /// What the window showed when a screen never became ready, as `failed/<name>.png` (not part of the pass).
    private func saveFailure(_ screen: SnapshotScreen, of window: UIWindow) {
        guard let dir = ProcessInfo.processInfo.environment["SCREENPASS_DIR"] else { return }
        let failed = URL(fileURLWithPath: dir).appendingPathComponent("failed", isDirectory: true)
        try? FileManager.default.createDirectory(at: failed, withIntermediateDirectories: true)
        try? Self.capture(window).write(to: failed.appendingPathComponent("\(screen.name).png"))
    }

    /// Stable: at least `minAge` seconds since the window appeared, and no pixel change for the last `quiet` seconds,
    /// sampled every 0.1 s (at most 5 s, then the last capture). Waiting for two identical captures alone is not
    /// enough: the glass bars, scroll-edge effects and sheet headers keep settling (by 1-4 colour levels, invisible) in
    /// steps for up to about 0.9 s after a window appears, with up to 0.5 s between steps.
    private func stableCapture(_ screen: SnapshotScreen, of window: UIWindow, mountedAt: Date,
                               minAge: TimeInterval = 1.0, quiet: TimeInterval = 0.2) async -> Data {
        var image = Self.snapshot(window)
        var pixels = Self.pixels(of: image)
        var lastChange = Date()
        let earliest = mountedAt.addingTimeInterval(minAge)
        let stableBy = lastChange.addingTimeInterval(5)
        while Date() < earliest || Date().timeIntervalSince(lastChange) < quiet {
            if Date() >= stableBy {
                print("screenpass: unstable \(screen.name)")
                break
            }
            await pump(0.1)
            let next = Self.snapshot(window)
            let nextPixels = Self.pixels(of: next)
            if nextPixels != pixels {
                (image, pixels) = (next, nextPixels)
                lastChange = Date()
            }
        }
        return image.pngData() ?? Data()
    }

    /// Lets the main run loop (SwiftUI updates, `.task`s, presentations) run for a while.
    private func pump(_ seconds: Double) async {
        try? await Task.sleep(for: .seconds(seconds))
    }

    /// The screen as the render server composited it: status bar, presented sheets and the glass / blur materials
    /// exactly as on the device (UIKit's private `_UICreateScreenUIImage`; `drawHierarchy` renders sheet glass
    /// without its blur and has no status bar). Cropped like the XCUITest shots: the bottom 1/50 of the height holds
    /// the home indicator, which the system fades on its own schedule. Falls back to `drawHierarchy` of the window.
    private static func snapshot(_ window: UIWindow) -> UIImage {
        guard let screen = screenImage()?.cgImage else { return drawHierarchy(window) }
        let size = CGSize(width: screen.width, height: screen.height - screen.height / 50)
        let format = UIGraphicsImageRendererFormat()
        format.scale = 1
        format.opaque = true
        return UIGraphicsImageRenderer(size: size, format: format).image { context in
            // UIKit's context is flipped; draw the CGImage upright, top-aligned, so the bottom strip falls off.
            let cg = context.cgContext
            cg.translateBy(x: 0, y: size.height)
            cg.scaleBy(x: 1, y: -1)
            cg.draw(screen, in: CGRect(x: 0, y: size.height - CGFloat(screen.height), width: CGFloat(screen.width),
                                       height: CGFloat(screen.height)))
        }
    }

    private typealias CreateScreenImage = @convention(c) () -> Unmanaged<UIImage>
    private static let createScreenImage: CreateScreenImage? = {
        guard let symbol = dlsym(UnsafeMutableRawPointer(bitPattern: -2), "_UICreateScreenUIImage") else {  // RTLD_DEFAULT
            print("screenpass: _UICreateScreenUIImage not found, capturing with drawHierarchy (no status bar, flat sheet glass)")
            return nil
        }
        return unsafeBitCast(symbol, to: CreateScreenImage.self)
    }()

    private static func screenImage() -> UIImage? { createScreenImage?().takeRetainedValue() }

    private static func drawHierarchy(_ window: UIWindow) -> UIImage {
        let format = UIGraphicsImageRendererFormat()
        format.scale = window.traitCollection.displayScale
        format.opaque = true
        return UIGraphicsImageRenderer(bounds: window.bounds, format: format).image { _ in
            window.drawHierarchy(in: window.bounds, afterScreenUpdates: true)
        }
    }

    private static func capture(_ window: UIWindow) -> Data { snapshot(window).pngData() ?? Data() }

    /// The raw bitmap, compared between captures (cheaper than encoding a PNG each time).
    private static func pixels(of image: UIImage) -> Data {
        image.cgImage?.dataProvider?.data.map { $0 as Data } ?? Data()
    }

    // MARK: Accessibility tree

    /// SwiftUI builds its accessibility elements (and so the identifiers) only while an accessibility client is active.
    /// XCUITest turns automation on from outside; in-process the same switch is libAccessibility's private
    /// `_AXSSetAutomationEnabled` (test target only, simulator). Without it a fresh simulator exposes no identifiers;
    /// the setting then sticks on that (dedicated) simulator.
    private static func enableAccessibility() {
        guard let handle = dlopen("/usr/lib/libAccessibility.dylib", RTLD_NOW) else {
            print("screenpass: could not load libAccessibility")
            return
        }
        typealias SetEnabled = @convention(c) (Int32) -> Void
        for name in ["_AXSSetAutomationEnabled", "_AXSApplicationAccessibilitySetEnabled"] {
            guard let symbol = dlsym(handle, name) else { print("screenpass: no \(name) in libAccessibility"); continue }
            unsafeBitCast(symbol, to: SetEnabled.self)(1)
        }
    }

    private static func contains(_ id: String, in window: UIWindow) -> Bool {
        var found = false
        walk(window) { if $0 == id { found = true } ; return !found }
        return found
    }

    private static func identifiers(in window: UIWindow) -> [String] {
        var ids: [String] = []
        walk(window) { ids.append($0); return true }
        return ids
    }

    /// SwiftUI's accessibility nodes answer `accessibilityIdentifier` without declaring `UIAccessibilityIdentification`.
    private static func identifier(of node: NSObject) -> String? {
        if let id = (node as? UIAccessibilityIdentification)?.accessibilityIdentifier { return id }
        let getter = NSSelectorFromString("accessibilityIdentifier")
        guard node.responds(to: getter) else { return nil }
        return node.perform(getter)?.takeUnretainedValue() as? String
    }

    /// Visits the accessibility identifier of every view and accessibility element under `root` (SwiftUI exposes its
    /// identifiers only as accessibility elements, not as views). `visit` returns false to stop.
    private static func walk(_ root: NSObject, _ visit: (String) -> Bool) {
        var seen = Set<ObjectIdentifier>()
        var stack: [NSObject] = [root]
        while let node = stack.popLast() {
            guard seen.insert(ObjectIdentifier(node)).inserted else { continue }
            if let id = identifier(of: node), !id.isEmpty, !visit(id) { return }
            if let elements = node.accessibilityElements {
                stack.append(contentsOf: elements.compactMap { $0 as? NSObject })
            }
            let count = node.accessibilityElementCount()
            if count != NSNotFound, count > 0 {
                stack.append(contentsOf: (0..<count).compactMap { node.accessibilityElement(at: $0) as? NSObject })
            }
            if let view = node as? UIView { stack.append(contentsOf: view.subviews) }
        }
    }
}
