import Foundation
import Observation

/// App settings. URL / gym / timer live in UserDefaults; the API token only in the Keychain.
@MainActor
@Observable
final class SettingsStore {
    private static let tokenAccount = "api-token"
    private let defaults: UserDefaults

    var backendURL: String {
        didSet { defaults.set(backendURL, forKey: "backendURL") }
    }
    var currentGymId: Int? {
        didSet { defaults.set(currentGymId, forKey: "currentGymId") }
    }
    var restSeconds: Int {
        didSet { defaults.set(restSeconds, forKey: "restSeconds") }
    }
    var apiToken: String {
        didSet { Keychain.set(apiToken, for: Self.tokenAccount) }
    }

    init(defaults: UserDefaults = .standard, loadToken: Bool = true) {
        self.defaults = defaults
        backendURL = defaults.string(forKey: "backendURL") ?? ""
        currentGymId = defaults.object(forKey: "currentGymId") as? Int
        let stored = defaults.integer(forKey: "restSeconds")
        restSeconds = stored > 0 ? stored : 150
        apiToken = loadToken ? (Keychain.get(Self.tokenAccount) ?? "") : ""
        #if DEBUG
        // Launch-argument override for end-to-end runs: `-backendURL http://localhost:8000 -debugApiToken <token>`
        // (never persisted to the Keychain).
        if loadToken, let t = defaults.string(forKey: "debugApiToken"), !t.isEmpty { apiToken = t }
        #endif
    }

    var isConfigured: Bool { APIClient.normalizedBaseURL(backendURL) != nil && !apiToken.isEmpty }

    func makeClient() -> APIClient? {
        guard let url = APIClient.normalizedBaseURL(backendURL) else { return nil }
        return APIClient(baseURL: url, token: apiToken.isEmpty ? nil : apiToken)
    }
}
