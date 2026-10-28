import SwiftUI

/// Behind the Coach tab's gear: the approval mode (the one coach setting the app can change), what the server runs
/// the advisor with (read-only), and the config version history.
struct CoachSettingsView: View {
    @Environment(AppEnvironment.self) private var env

    var body: some View {
        let coach = env.coach
        ScrollView {
            VStack(alignment: .leading, spacing: Kit.groupSpacing) {
                PageSection("Proposals", info: "How much the coach may apply without asking. Every applied change creates a config version you can roll back.") {
                    Card { ApprovalModePicker() }
                }
                PageSection("Advisor", info: "These are set on the server (advisor config and environment) and are shown here read-only.") {
                    advisorCard(coach.settings)
                }
                PageSection("Config") {
                    Card(padding: 0) {
                        CoachRowLink(screen: .configHistory) {
                            HStack(spacing: 10) {
                                Image(systemName: "clock.arrow.circlepath").foregroundStyle(Domain.coach.color)
                                    .accessibilityHidden(true)
                                Text("Config History").foregroundStyle(.primary)
                                Spacer(minLength: 8)
                                if let v = coach.activeVersion {
                                    Text(CoachLogic.versionLabel(v.version)).monospacedDigit().foregroundStyle(.secondary)
                                }
                            }
                        }
                        .padding(.horizontal, 16)
                    }
                }
                CoachFreshnessLabel(repository: coach)
            }
            .pagePadding()
        }
        .screenStyle()
        .navigationTitle("Coach Settings")
        .navigationBarTitleDisplayMode(.inline)
        .refreshable {
            async let s: Void = coach.refreshSettings()
            async let h: Void = coach.refreshHistory()
            _ = await (s, h)
        }
    }

    @ViewBuilder
    private func advisorCard(_ settings: CoachSettingsDTO?) -> some View {
        Card(padding: 0) {
            VStack(spacing: 0) {
                if let settings {
                    CoachValueRow(title: "Status", value: settings.isConnected ? "Connected" : "Not connected",
                                  color: settings.isConnected ? .green : .orange)
                    if let runsOn = Self.runsOn(settings) {
                        Divider()
                        CoachValueRow(title: "Runs On", value: runsOn)
                    }
                    if let model = settings.model, !model.isEmpty {
                        Divider()
                        CoachValueRow(title: "Model", value: model)
                    }
                    Divider()
                    CoachValueRow(title: "Check-ins per Week", value: settings.maxCallsPerWeek > 0 ? "\(settings.maxCallsPerWeek)" : "Off")
                    Divider()
                    CoachValueRow(title: "Dry Run", value: settings.dryRun ? "On" : "Off",
                                  color: settings.dryRun ? .indigo : .secondary)
                } else {
                    Text(env.coach.canFetch ? "Loading the coach settings…" : "Add the backend URL and API token in Settings first.")
                        .font(.subheadline).foregroundStyle(.secondary)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .padding(.vertical, 12)
                }
            }
            .padding(.horizontal, 16)
        }
    }

    /// "Claude API" / "Claude subscription", nil when the server does not say.
    static func runsOn(_ s: CoachSettingsDTO) -> String? {
        switch s.provider ?? s.billing {
        case "api": "Claude API"
        case "claude_code", "subscription": "Claude subscription"
        case "none": "No credential"
        default: nil
        }
    }
}
