import FitnessCore
import AVFoundation
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

/// Identifiers and wording of the workout's local notifications.
enum WorkoutNotifications {
    static let restId = "rest-timer"
    static let finishReminderId = "finish-reminder"
    /// A short double ring of a boxing bell (under a second), in the app bundle (also used as the notification sound).
    static let bellFile = "boxing_bell.wav"
    /// After the last set's rest ends with nothing logged, remind to press Finish this much later.
    static let finishReminderDelay: TimeInterval = 180

    static var all: [String] { [restId, finishReminderId] }

    /// How a notification shows while the app is open: the rest notification not at all (the bell rings in the
    /// app instead), anything else as a banner with sound. Outside the app the system always shows them.
    static func inAppPresentation(forId id: String) -> UNNotificationPresentationOptions {
        id == restId ? [] : [.banner, .list, .sound]
    }
}

/// In-app rest timer based on an absolute end date (so it stays correct across backgrounding).
/// Schedules a local notification (bell sound) for the end; when the app is open at the end it rings the bell itself
/// (ambient audio: silent with the ring switch off unless headphones are connected, mixes with music).
/// `onEvent` is the seam for a future Live Activity.
@MainActor
@Observable
final class RestTimer {
    private(set) var endDate: Date?
    private(set) var totalSeconds: TimeInterval = 0
    /// Variant id of the exercise whose set started the running rest (nil: not tied to an exercise).
    private(set) var variantId: Int?
    /// When the "finish your workout" reminder is due (nil: none). Set after the workout's last open set was ticked.
    private(set) var finishReminderDate: Date?
    @ObservationIgnored private var startDate: Date?

    @ObservationIgnored var onEvent: ((RestTimerEvent) -> Void)?
    @ObservationIgnored private var finishTask: Task<Void, Never>?
    @ObservationIgnored private let center: UNUserNotificationCenter?
    @ObservationIgnored private let silent: Bool
    @ObservationIgnored private let bell: BellPlayer

    /// `silent`: no system notification center and no sound (unit tests run hosted in the app); an injected `center`
    /// is still used.
    init(center: UNUserNotificationCenter? = nil, silent: Bool = false) {
        self.center = center
        self.silent = silent
        self.bell = BellPlayer(enabled: !silent)
    }

    var isRunning: Bool { endDate != nil }

    func remaining(at date: Date = AppClock.instant) -> TimeInterval {
        max(0, (endDate ?? date).timeIntervalSince(date))
    }

    /// `finishReminder`: this rest follows the last open set of the workout.
    func start(seconds: Int, variantId: Int? = nil, finishReminder: Bool = false) {
        let total = TimeInterval(max(5, seconds))
        totalSeconds = total
        self.variantId = variantId
        startDate = AppClock.instant
        let end = AppClock.instant.addingTimeInterval(total)
        if finishReminder { finishReminderDate = end } else { cancelFinishReminder() }
        arm(end: end)
        onEvent?(.started(end: end, total: totalSeconds))
        Task { await requestAuthorizationIfNeeded() }
    }

    func adjust(by delta: TimeInterval) {
        guard let end = endDate else { return }
        let newEnd = max(AppClock.instant.addingTimeInterval(1), end.addingTimeInterval(delta))
        totalSeconds = max(1, totalSeconds + delta)
        arm(end: newEnd)
        onEvent?(.adjusted(end: newEnd, total: totalSeconds))
    }

    /// The exercise's rest time was changed while this rest runs: count the running rest as `seconds` in total
    /// (measured from when it started, so ±15 s taps made so far are replaced). Ignored for another exercise.
    func retarget(variantId: Int, to seconds: Int, now: Date = AppClock.instant) {
        guard endDate != nil, self.variantId == variantId, let start = startDate else { return }
        let newEnd = max(now.addingTimeInterval(1), start.addingTimeInterval(TimeInterval(seconds)))
        totalSeconds = max(1, newEnd.timeIntervalSince(start))
        arm(end: newEnd)
        onEvent?(.adjusted(end: newEnd, total: totalSeconds))
    }

    /// Skip the rest. A pending finish reminder counts from now (the rest is over).
    func cancel() {
        guard endDate != nil else { return }
        clear()
        notificationCenter?.removePendingNotificationRequests(withIdentifiers: [WorkoutNotifications.restId])
        if finishReminderDate != nil { scheduleFinishReminder(restEnd: AppClock.instant) }
        onEvent?(.cancelled)
    }

    /// The workout was finished / discarded, or it has open sets again.
    func cancelFinishReminder() {
        guard finishReminderDate != nil else { return }
        finishReminderDate = nil
        notificationCenter?.removePendingNotificationRequests(withIdentifiers: [WorkoutNotifications.finishReminderId])
        notificationCenter?.removeDeliveredNotifications(withIdentifiers: [WorkoutNotifications.finishReminderId])
    }

    private func arm(end: Date) {
        endDate = end
        finishTask?.cancel()
        scheduleNotification(at: end)
        if finishReminderDate != nil { scheduleFinishReminder(restEnd: end) }
        finishTask = Task { [weak self] in
            let wait = end.timeIntervalSince(AppClock.instant)
            if wait > 0 { try? await Task.sleep(for: .seconds(wait)) }
            guard !Task.isCancelled else { return }
            self?.fire()
        }
    }

    private func fire() {
        guard let end = endDate, end <= AppClock.instant.addingTimeInterval(0.5) else { return }
        // Only ring on time with the app open. After a suspension this runs late, and the notification already rang.
        if AppClock.instant.timeIntervalSince(end) < 3, UIApplication.shared.applicationState == .active {
            UINotificationFeedbackGenerator().notificationOccurred(.success)
            bell.play()
        }
        // The pending notification is left alone: it is due now, and removing it here could race its delivery.
        clear()
        onEvent?(.finished)
    }

    private func clear() {
        endDate = nil
        variantId = nil
        startDate = nil
        finishTask?.cancel()
        finishTask = nil
    }

    // MARK: Notifications

    private var notificationCenter: UNUserNotificationCenter? {
        // Only touch the real center when explicitly injected or when not silent (unit tests run hosted in the app).
        center ?? (silent ? nil : .current())
    }

    /// Asks once (the system remembers the answer). Called at launch too, so the first rest's notification is not
    /// scheduled before permission exists.
    func requestAuthorizationIfNeeded() async {
        guard let center = notificationCenter else { return }
        #if DEBUG
        if UITestSupport.skipsNotificationPrompt { return }  // the system alert would cover the UI-test screenshots
        #endif
        let settings = await center.notificationSettings()
        guard settings.authorizationStatus == .notDetermined else { return }
        let granted = (try? await center.requestAuthorization(options: [.alert, .sound])) ?? false
        // the rest that triggered the prompt was scheduled before permission existed: schedule it again
        if granted, let end = endDate { scheduleNotification(at: end) }
        if granted, let end = endDate, finishReminderDate != nil { scheduleFinishReminder(restEnd: end) }
    }

    private func scheduleNotification(at end: Date) {
        let content = UNMutableNotificationContent()
        content.title = "Rest over"
        content.body = "Time for your next set."
        content.sound = UNNotificationSound(named: UNNotificationSoundName(WorkoutNotifications.bellFile))
        content.interruptionLevel = .active
        add(WorkoutNotifications.restId, content, at: end)
    }

    private func scheduleFinishReminder(restEnd: Date) {
        let due = restEnd.addingTimeInterval(WorkoutNotifications.finishReminderDelay)
        finishReminderDate = due
        let content = UNMutableNotificationContent()
        content.title = "Workout done?"
        content.body = "All sets are logged. Open the app and tap Finish to save your workout."
        content.sound = .default
        content.interruptionLevel = .active
        add(WorkoutNotifications.finishReminderId, content, at: due)
    }

    private func add(_ id: String, _ content: UNNotificationContent, at date: Date) {
        guard let center = notificationCenter else { return }
        center.removePendingNotificationRequests(withIdentifiers: [id])
        center.removeDeliveredNotifications(withIdentifiers: [id])
        let trigger = UNTimeIntervalNotificationTrigger(timeInterval: max(1, date.timeIntervalSince(AppClock.instant)), repeats: false)
        center.add(UNNotificationRequest(identifier: id, content: content, trigger: trigger))
    }
}

/// Rings the bell inside the app, on top of whatever else is playing. Playback + mix-with-others: the bell is heard
/// over Spotify / Apple Music (also with the silent switch on) and never pauses or ducks them. The session is released
/// again once the bell has rung so it does not linger as the app's audio claim.
@MainActor
final class BellPlayer {
    private let enabled: Bool
    private var player: AVAudioPlayer?
    private var releaseTask: Task<Void, Never>?

    init(enabled: Bool = true) { self.enabled = enabled }

    func play() {
        guard enabled else { return }
        if player == nil {
            let name = (WorkoutNotifications.bellFile as NSString).deletingPathExtension
            let ext = (WorkoutNotifications.bellFile as NSString).pathExtension
            guard let url = Bundle.main.url(forResource: name, withExtension: ext) else { return }
            player = try? AVAudioPlayer(contentsOf: url)
            player?.prepareToPlay()
        }
        guard let player else { return }
        let session = AVAudioSession.sharedInstance()
        try? session.setCategory(.playback, mode: .default, options: [.mixWithOthers])
        try? session.setActive(true)
        player.currentTime = 0
        player.play()
        releaseTask?.cancel()
        releaseTask = Task {
            try? await Task.sleep(for: .seconds(player.duration + 0.3))
            guard !Task.isCancelled else { return }
            // no notifyOthersOnDeactivation: nothing was interrupted, so there is nothing for others to resume
            try? session.setActive(false)
        }
    }
}

/// Presents workout notifications: the rest bell is not shown while the app is open (it rings in the app), the finish
/// reminder is. Set as the notification center's delegate at launch.
final class NotificationPresenter: NSObject, UNUserNotificationCenterDelegate, Sendable {
    func userNotificationCenter(_ center: UNUserNotificationCenter, willPresent notification: UNNotification) async
        -> UNNotificationPresentationOptions {
        WorkoutNotifications.inAppPresentation(forId: notification.request.identifier)
    }
}
