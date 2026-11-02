import Foundation

/// Rest-timer times: one default (2:00) and a per-exercise override that sticks.
///
/// The truth lives on the server (`exercise_overrides.<slug>.rest_s`, contract: docs/api/exercises.md); the app
/// keeps a `LocalRestOverride` per variant id so a change applies at once, works offline and is sent with the
/// next sync. A pull reconciles the rows with the server (see `reconcile`).
public enum RestTime: Sendable {
    /// Used when neither the plan nor the synced config says anything.
    public static let fallbackSeconds = 120
    /// The picker offers 1:00 to 5:00 in 15 s steps (the server accepts 30...600; a value outside the
    /// range, e.g. from the coach, is still shown and kept as the current choice).
    public static let pickerRange = 60...300
    public static let pickerStep = 15

    public static var pickerValues: [Int] { Array(stride(from: pickerRange.lowerBound, through: pickerRange.upperBound, by: pickerStep)) }

    /// Picker choices for the current value: `nil` is "Default"; a current value outside the list is added.
    public static func pickerChoices(current: Int?) -> [Int] {
        var values = pickerValues
        if let current, !values.contains(current) { values.append(current); values.sort() }
        return values
    }

    /// "2:00", "3:15"
    public static func format(_ seconds: Int) -> String { "\(seconds / 60):" + String(format: "%02d", seconds % 60) }

    public static func defaultLabel(default seconds: Int) -> String { "Default (\(format(seconds)))" }

}
