#if DEBUG
import FitnessCore
import SwiftData
import SwiftUI
import UIKit

/// Launch arguments of the screenshot / UI-test pass (UserDefaults argument domain, DEBUG builds only), so every run
/// renders the same pixels. Combine with `-uiDemo YES` and `-uiTab ...` (AppEnvironment.swift):
/// - `-uiReset YES`: wipes local state before anything loads (see `resetIfRequested`), and skips the notification
///   permission request so no system alert covers the screen.
/// - `-uiNoAnimations YES`: no UIKit or SwiftUI animations.
/// - `-uiFixedNow 2026-11-04T09:41:00+01:00`: freezes `AppClock` at that instant; the demo week is the week containing it.
/// - `-uiTimeZone Europe/Berlin`: the default time zone, set before any formatter is created.
enum UITestSupport {
    private static var defaults: UserDefaults { .standard }
    nonisolated(unsafe) private static var configured = false

    /// Time zone, clock and animations. Runs first in `AppEnvironment.init`, once.
    @MainActor
    static func configureLaunch() {
        guard !configured else { return }
        configured = true
        if let id = defaults.string(forKey: "uiTimeZone"), let zone = TimeZone(identifier: id) { NSTimeZone.default = zone }
        if let text = defaults.string(forKey: "uiFixedNow") { AppClock.fixedNow = parse(text) }
        if animationsDisabled { UIView.setAnimationsEnabled(false) }
    }

    static var animationsDisabled: Bool { defaults.bool(forKey: "uiNoAnimations") }

    /// `-uiReset YES` (only the UI tests pass it) also means no notification permission alert over the screenshots.
    static var skipsNotificationPrompt: Bool { defaults.bool(forKey: "uiReset") }

    private static func parse(_ text: String) -> Date? {
        let plain = ISO8601DateFormatter()
        let fractional = ISO8601DateFormatter()
        fractional.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return plain.date(from: text) ?? fractional.date(from: text)
    }

    /// `-uiReset YES`: demo and synced rows, plan / exercise / gym caches (via the backend reset), then what that keeps:
    /// unsynced local workouts and weigh-ins (the outbox), the stats, coach and gym JSON caches, and the debug / demo
    /// preferences. The Keychain token and the backend URL stay. Runs before `-uiDemo` loads, so the demo starts fresh.
    @MainActor
    static func resetIfRequested(_ env: AppEnvironment) {
        guard defaults.bool(forKey: "uiReset") else { return }
        env.backend.clearDemoData()
        try? env.context.delete(model: LocalSession.self)
        try? env.context.delete(model: LocalSet.self)
        try? env.context.delete(model: LocalBodyMeasurement.self)
        try? env.context.save()
        env.stats.reset()
        for key in [AppClock.overrideKey, LastSession.defaultsKey, SummaryLogic.pinnedKey, TemplateSync.key] {
            defaults.removeObject(forKey: key)
        }
    }
}

/// Root transaction for `-uiNoAnimations`: whatever `withAnimation` / `.animation` asks for, nothing animates.
private struct NoAnimationsTransaction: ViewModifier {
    func body(content: Content) -> some View {
        content.transaction { transaction in
            guard UITestSupport.animationsDisabled else { return }
            transaction.disablesAnimations = true
            transaction.animation = nil
        }
    }
}

extension View {
    func uiTestHooks() -> some View { modifier(NoAnimationsTransaction()) }
}
#endif
