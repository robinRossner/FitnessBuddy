import UIKit

/// How "Open Garmin Connect" reaches the Garmin Connect phone app. Opening it usually makes the watch upload its
/// activities (the app is what talks to the watch). The one place to change if the scheme turns out wrong.
enum GarminConnectLink {
    /// Tried in order. `gcm-ciq://` is only community-documented (at most it opens the app's start page); the App Store
    /// page is the fallback when the scheme does not open (app not installed). No `canOpenURL`, so no
    /// LSApplicationQueriesSchemes entry is needed.
    static let urls: [URL] = [
        URL(string: "gcm-ciq://")!,
        URL(string: "https://apps.apple.com/app/garmin-connect/id583446403")!,
    ]

    /// Opens the first URL that works; false when none did.
    @MainActor
    static func open() async -> Bool {
        for url in urls where await UIApplication.shared.open(url) { return true }
        return false
    }
}
