import FitnessCore
import Foundation
import Observation

/// App settings. URL / gym live in UserDefaults; the API token only in the Keychain.
@MainActor
@Observable
final class SettingsStore {
    private static let tokenAccount = "api-token"
    private let defaults: UserDefaults
    private let persistToken: Bool
    /// False when the last Keychain write failed (the token then only lives in memory until the app quits).
    private(set) var tokenStored = true

    var backendURL: String {
        didSet { defaults.set(backendURL, forKey: "backendURL") }
    }
    var currentGymId: Int? {
        didSet { defaults.set(currentGymId, forKey: "currentGymId") }
    }
    var apiToken: String {
        didSet { if persistToken { tokenStored = Keychain.set(apiToken, for: Self.tokenAccount) } }
    }

    init(defaults: UserDefaults = .standard, loadToken: Bool = true) {
        self.defaults = defaults
        self.persistToken = loadToken
        backendURL = defaults.string(forKey: "backendURL") ?? ""
        currentGymId = defaults.object(forKey: "currentGymId") as? Int
        apiToken = loadToken ? (Keychain.get(Self.tokenAccount) ?? "") : ""
        #if DEBUG
        // Launch-argument override for end-to-end runs: `-backendURL http://localhost:8000 -debugApiToken <token>`
        // (never persisted to the Keychain).
        if loadToken, let t = defaults.string(forKey: "debugApiToken"), !t.isEmpty { apiToken = t }
        #endif
    }

    /// Applies the backend fields typed in Settings (whitespace / newlines from pasting are stripped).
    /// Returns false if the token could not be written to the Keychain.
    @discardableResult
    func update(url: String, token: String) -> Bool {
        backendURL = url.trimmingCharacters(in: .whitespacesAndNewlines)
        apiToken = token.trimmingCharacters(in: .whitespacesAndNewlines)
        return tokenStored
    }

    /// Identifies the backend + token the local cache belongs to.
    var fingerprint: String? { BackendReset.fingerprint(url: backendURL, token: apiToken) }

    var isConfigured: Bool { APIClient.normalizedBaseURL(backendURL) != nil && !apiToken.isEmpty }

    func makeClient() -> APIClient? {
        guard let url = APIClient.normalizedBaseURL(backendURL) else { return nil }
        let token = apiToken.trimmingCharacters(in: .whitespacesAndNewlines)
        return APIClient(baseURL: url, token: token.isEmpty ? nil : token)
    }
}
