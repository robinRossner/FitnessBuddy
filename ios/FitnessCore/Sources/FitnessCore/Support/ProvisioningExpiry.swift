import Foundation

/// When a sideloaded install stops launching: the `ExpirationDate` of the provisioning profile inside the app
/// (7 days on a free Apple account). Simulator and App Store builds carry no profile, so `installed` is nil there.
public enum ProvisioningExpiry: Sendable {
    /// From this many days left the app shows the tag at the top, not only the row in Settings.
    public static let warningDays = 2

    public static let installed: Date? = {
        guard let url = Bundle.main.url(forResource: "embedded", withExtension: "mobileprovision"),
              let data = try? Data(contentsOf: url) else { return nil }
        return expirationDate(fromProfile: data)
    }()

    /// A profile is a signed CMS envelope with the XML plist embedded as plain bytes.
    public static func expirationDate(fromProfile data: Data) -> Date? {
        guard let start = data.range(of: Data("<?xml".utf8)),
              let end = data.range(of: Data("</plist>".utf8), in: start.upperBound..<data.endIndex) else { return nil }
        let xml = data.subdata(in: start.lowerBound..<end.upperBound)
        let plist = try? PropertyListSerialization.propertyList(from: xml, format: nil) as? [String: Any]
        return plist?["ExpirationDate"] as? Date
    }

    /// Calendar days, so "tomorrow" means tomorrow whatever the time of day. Never negative.
    public static func daysLeft(until expiry: Date, from now: Date, calendar: Calendar = .current) -> Int {
        let days = calendar.dateComponents([.day], from: calendar.startOfDay(for: now),
                                           to: calendar.startOfDay(for: expiry)).day ?? 0
        return max(days, 0)
    }

    /// "today", "tomorrow", "in 5 days".
    public static func label(daysLeft: Int) -> String {
        switch daysLeft {
        case ...0: "today"
        case 1: "tomorrow"
        default: "in \(daysLeft) days"
        }
    }
}
