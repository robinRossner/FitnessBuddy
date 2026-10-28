import SwiftUI

/// The three approval modes as radio rows with a one-line explanation each. Changing it is a PUT, so it is disabled
/// offline (with a hint). Used in the Coach tab and in Settings.
struct ApprovalModePicker: View {
    @Environment(AppEnvironment.self) private var env
    @State private var saving: ApprovalMode?
    @State private var error: String?

    private var current: ApprovalMode? { env.coach.settings?.approvalMode }
    private var enabled: Bool { env.coach.canWrite && env.coach.settings != nil && saving == nil }

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            ForEach(ApprovalMode.choices) { mode in
                Button { select(mode) } label: {
                    HStack(alignment: .top, spacing: 12) {
                        Image(systemName: current == mode ? "checkmark.circle.fill" : "circle")
                            .font(.title3)
                            .foregroundStyle(current == mode ? Domain.coach.color : .secondary)
                            .frame(width: 24)
                        VStack(alignment: .leading, spacing: 2) {
                            Text(mode.title).font(.body.weight(.medium)).foregroundStyle(.primary)
                            Text(mode.explanation).font(.footnote).foregroundStyle(.secondary)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                        Spacer(minLength: 0)
                        if saving == mode { ProgressView().controlSize(.small) }
                    }
                    .padding(.vertical, 6)
                    .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .disabled(!enabled)
                .accessibilityAddTraits(current == mode ? .isSelected : [])
            }
            if let hint = env.coach.writeBlockedHint {
                OfflineHint(text: hint).padding(.top, 4)
            } else if env.coach.settings == nil {
                Text("Loading the coach settings…").font(.footnote).foregroundStyle(.secondary).padding(.top, 4)
            }
            if let error { Text(error).font(.footnote).foregroundStyle(.red).padding(.top, 4) }
        }
        .task { if env.coach.settings == nil { await env.coach.load() } }
    }

    private func select(_ mode: ApprovalMode) {
        guard mode != current else { return }
        saving = mode
        error = nil
        Task {
            do { try await env.coach.setApprovalMode(mode) }
            catch { self.error = CoachError.map(error).errorDescription }
            saving = nil
        }
    }
}
