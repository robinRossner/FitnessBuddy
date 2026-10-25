import Foundation
import Observation
import UIKit
import UserNotifications

enum RestTimerEvent: Equatable {
    case started(end: Date, total: TimeInterval)
    case adjusted(end: Date, total: TimeInterval)
    case finished
    case cancelled
}

/// In-app rest timer based on an absolute end date (so it stays correct across backgrounding).
/// Schedules a local notification for the end. `onEvent` is the seam for a future Live Activity:
/// start/update/end an `Activity` from there without touching the views.
@MainActor
@Observable
final class RestTimer {
    private(set) var endDate: Date?
    private(set) var totalSeconds: TimeInterval = 0
    /// Variant id of the exercise whose set started the running rest (nil: not tied to an exercise).
    private(set) var variantId: Int?
    @ObservationIgnored private var startDate: Date?

    @ObservationIgnored var onEvent: ((RestTimerEvent) -> Void)?
    @ObservationIgnored private var finishTask: Task<Void, Never>?
    @ObservationIgnored private let notificationId = "rest-timer"
    @ObservationIgnored private let center: UNUserNotificationCenter?

    init(center: UNUserNotificationCenter? = nil) {
        self.center = center
    }

    var isRunning: Bool { endDate != nil }

    func remaining(at date: Date = .now) -> TimeInterval {
        max(0, (endDate ?? date).timeIntervalSince(date))
    }

    func start(seconds: Int, variantId: Int? = nil) {
        let total = TimeInterval(max(5, seconds))
        totalSeconds = total
        self.variantId = variantId
        startDate = Date()
        arm(end: Date().addingTimeInterval(total))
        onEvent?(.started(end: endDate!, total: totalSeconds))
        requestAuthorizationIfNeeded()
    }

    func adjust(by delta: TimeInterval) {
        guard let end = endDate else { return }
        let newEnd = max(Date().addingTimeInterval(1), end.addingTimeInterval(delta))
        totalSeconds = max(1, totalSeconds + delta)
        arm(end: newEnd)
        onEvent?(.adjusted(end: newEnd, total: totalSeconds))
    }

    /// The exercise's rest time was changed while this rest runs: count the running rest as `seconds` in total
    /// (measured from when it started, so ±15 s taps made so far are replaced). Ignored for another exercise.
    func retarget(variantId: Int, to seconds: Int, now: Date = Date()) {
        guard endDate != nil, self.variantId == variantId, let start = startDate else { return }
        let newEnd = max(now.addingTimeInterval(1), start.addingTimeInterval(TimeInterval(seconds)))
        totalSeconds = max(1, newEnd.timeIntervalSince(start))
        arm(end: newEnd)
        onEvent?(.adjusted(end: newEnd, total: totalSeconds))
    }

    func cancel() {
        guard endDate != nil else { return }
        clear()
        onEvent?(.cancelled)
    }

    private func arm(end: Date) {
        endDate = end
        finishTask?.cancel()
        scheduleNotification(at: end)
        finishTask = Task { [weak self] in
            let wait = end.timeIntervalSinceNow
            if wait > 0 { try? await Task.sleep(for: .seconds(wait)) }
            guard !Task.isCancelled else { return }
            self?.fire()
        }
    }

    private func fire() {
        guard let end = endDate, end <= Date().addingTimeInterval(0.5) else { return }
        UINotificationFeedbackGenerator().notificationOccurred(.success)
        clear()
        onEvent?(.finished)
    }

    private func clear() {
        endDate = nil
        variantId = nil
        startDate = nil
        finishTask?.cancel()
        finishTask = nil
        notificationCenter?.removePendingNotificationRequests(withIdentifiers: [notificationId])
    }

    // MARK: Notifications

    private var notificationCenter: UNUserNotificationCenter? {
        // Unit tests run hosted in the app; only touch the real center when explicitly injected or in a real run.
        center ?? (ProcessInfo.processInfo.environment["XCTestConfigurationFilePath"] == nil ? .current() : nil)
    }

    private func requestAuthorizationIfNeeded() {
        notificationCenter?.requestAuthorization(options: [.alert, .sound]) { _, _ in }
    }

    private func scheduleNotification(at end: Date) {
        guard let center = notificationCenter else { return }
        center.removePendingNotificationRequests(withIdentifiers: [notificationId])
        let content = UNMutableNotificationContent()
        content.title = "Rest over"
        content.body = "Time for your next set."
        content.sound = .default
        content.interruptionLevel = .active
        let interval = max(1, end.timeIntervalSinceNow)
        let trigger = UNTimeIntervalNotificationTrigger(timeInterval: interval, repeats: false)
        center.add(UNNotificationRequest(identifier: notificationId, content: content, trigger: trigger))
    }
}
