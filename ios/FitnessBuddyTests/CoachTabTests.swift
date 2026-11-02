import Foundation
import SwiftData
import Testing
@testable import FitnessBuddy
@testable import FitnessCore

/// App-side parts of the Coach tab (navigation, settings view rules).
@Suite(.tags(.coach, .fast))
struct CoachTabAppTests {
    @Test func appRoutesMapToCoachScreens() {
        #expect(CoachScreen(.proposal("7")) == .proposal("7"))
        #expect(CoachScreen(.history) == .configHistory)
    }

    @Test func providerKeysDecodeAndDriveConnection() throws {
        let json = #"{"provider":"claude_code","credential_configured":true,"billing":"subscription","model":"claude-opus-5-5","approval_mode":"manual","api_key_configured":false,"dry_run":false,"calls_this_week":1,"max_calls_per_week":4}"#
        let s = try CoachCoding.makeDecoder().decode(CoachSettingsDTO.self, from: Data(json.utf8))
        #expect(s.isConnected && !s.apiKeyConfigured)
        #expect(s.model == "claude-opus-5-5" && s.billing == "subscription")
        #expect(CoachSettingsView.runsOn(s) == "Claude subscription")
    }

    @Test func olderServersFallBackToTheApiKeyFlag() throws {
        // what an older server sends: no provider keys
        let old = #"{"approval_mode":"manual","api_key_configured":true,"dry_run":false,"calls_this_week":0,"max_calls_per_week":4}"#
        let s = try CoachCoding.makeDecoder().decode(CoachSettingsDTO.self, from: Data(old.utf8))
        #expect(s.credentialConfigured == nil && s.isConnected)
        #expect(CoachSettingsView.runsOn(s) == nil)
        #expect(!CoachSettingsDTO(apiKeyConfigured: false).isConnected)
        #expect(CoachSettingsView.runsOn(CoachSettingsDTO(provider: "api")) == "Claude API")
    }
}
