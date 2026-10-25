import SwiftUI

/// The config version timeline: who changed what and why, how it turned out, and a way back.
struct CoachHistoryView: View {
    @Environment(AppEnvironment.self) private var env
    @State private var rollbackTarget: CoachVersionDTO?
    @State private var rollingBack = false
    @State private var error: String?

    var body: some View {
        let coach = env.coach
        ScrollView {
            VStack(spacing: 14) {
                if coach.history.isEmpty {
                    Card {
                        Label(coach.isOffline ? "Offline" : "No versions yet", systemImage: "clock.arrow.2.circlepath").font(.headline)
                        Text(coach.isOffline ? "Nothing cached yet. Connect once and this stays available offline."
                                             : "Every change to your training settings shows up here.")
                            .font(.subheadline).foregroundStyle(.secondary)
                    }
                } else {
                    ForEach(coach.history) { version in
                        VersionCard(version: version, canRollBack: !version.active, busy: rollingBack,
                                    rollback: { rollbackTarget = version })
                    }
                }
                if let hint = coach.writeBlockedHint { OfflineHint(text: hint) }
                if let error { Label(error, systemImage: "exclamationmark.triangle.fill").font(.footnote).foregroundStyle(.orange) }
                FreshnessLine(fetchedAt: coach.historyFetchedAt, offline: coach.isOffline)
            }
            .padding(.horizontal, 16)
            .padding(.vertical, 12)
        }
        .background(Color.screenBackground)
        .navigationTitle("Config history")
        .navigationBarTitleDisplayMode(.inline)
        .refreshable { await coach.refreshHistory() }
        .task { await coach.load(generation: env.sync.generation); await coach.refreshHistory() }
        .confirmationDialog(
            rollbackTarget.map { "Roll back to \(CoachLogic.versionLabel($0.version))?" } ?? "Roll back?",
            isPresented: Binding(get: { rollbackTarget != nil }, set: { if !$0 { rollbackTarget = nil } }),
            titleVisibility: .visible
        ) {
            Button("Roll back", role: .destructive) { if let target = rollbackTarget { rollback(target) } }
            Button("Cancel", role: .cancel) { rollbackTarget = nil }
        } message: {
            Text("This creates a new version with these settings and re-plans your sessions. Logged workouts stay as they are.")
        }
    }

    private func rollback(_ version: CoachVersionDTO) {
        rollbackTarget = nil
        rollingBack = true
        error = nil
        Task {
            do { try await env.coach.rollback(to: version.version) }
            catch { self.error = CoachError.map(error).errorDescription }
            rollingBack = false
        }
    }
}

private struct FreshnessLine: View {
    let fetchedAt: Date?
    let offline: Bool

    var body: some View {
        if let text = StatsFreshness.label(fetchedAt: fetchedAt, offline: offline) {
            Text(text).font(.caption).foregroundStyle(.secondary).frame(maxWidth: .infinity)
        }
    }
}

private struct VersionCard: View {
    @Environment(AppEnvironment.self) private var env
    let version: CoachVersionDTO
    let canRollBack: Bool
    let busy: Bool
    let rollback: () -> Void

    var body: some View {
        Card(tint: version.active ? CoachStyle.color : nil) {
            HStack(alignment: .center, spacing: 10) {
                Image(systemName: version.author.symbol)
                    .font(.system(size: 15, weight: .semibold))
                    .foregroundStyle(version.author == .advisor ? CoachStyle.color : .secondary)
                    .frame(width: 30, height: 30)
                    .background((version.author == .advisor ? CoachStyle.color : Color.secondary).opacity(0.14), in: Circle())
                VStack(alignment: .leading, spacing: 1) {
                    HStack(spacing: 6) {
                        Text(CoachLogic.versionLabel(version.version)).font(.headline)
                        if version.active { Chip(text: "Active", color: .green) }
                    }
                    Text([version.author.title, version.createdAt?.coachLabel].compactMap { $0 }.joined(separator: " · "))
                        .font(.caption).foregroundStyle(.secondary)
                }
                Spacer()
            }
            if let reason = version.reason, !reason.isEmpty {
                Text(reason).font(.subheadline).fixedSize(horizontal: false, vertical: true)
            }
            if !version.diff.isEmpty {
                VStack(alignment: .leading, spacing: 8) {
                    ForEach(version.diff) { d in
                        VStack(alignment: .leading, spacing: 3) {
                            Text(d.path).font(.caption.monospaced()).foregroundStyle(.secondary).lineLimit(2)
                            ChangeValueText(text: d.changeText)
                        }
                    }
                }
            }
            if let outcome = version.outcome { OutcomeBadge(outcome: outcome, path: nil) }
            HStack(spacing: 12) {
                if let id = version.consultationId {
                    Button { env.openCoach(proposal: id) } label: {
                        Label("View proposal", systemImage: "bubble.left.and.text.bubble.right").font(.footnote.weight(.semibold))
                    }
                    .buttonStyle(.plain).foregroundStyle(CoachStyle.color)
                }
                Spacer()
                if canRollBack {
                    Button { rollback() } label: {
                        Label("Roll back to this version", systemImage: "arrow.uturn.backward").font(.footnote.weight(.semibold))
                    }
                    .buttonStyle(.bordered).tint(CoachStyle.color)
                    .disabled(busy || env.coach.writeBlockedHint != nil)
                }
            }
        }
        .accessibilityElement(children: .contain)
    }
}

/// "Improved: weekly km up 22 %", with the before / after metric when the server sent one.
struct OutcomeBadge: View {
    let outcome: CoachOutcomeDTO
    let path: String?

    var body: some View {
        HStack(alignment: .top, spacing: 8) {
            Image(systemName: outcome.verdict.symbol).foregroundStyle(outcome.verdict.color)
            VStack(alignment: .leading, spacing: 2) {
                Text(outcome.verdict.title).font(.subheadline.weight(.semibold)).foregroundStyle(outcome.verdict.color)
                if let notes = outcome.notes, !notes.isEmpty {
                    Text(notes).font(.footnote).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                }
                ForEach(CoachOutcomeDTO.metricRows(before: outcome.metricBefore, after: outcome.metricAfter, path: path), id: \.name) { row in
                    HStack(spacing: 6) {
                        Text(row.name).foregroundStyle(.secondary)
                        Spacer(minLength: 8)
                        Text(row.change).monospacedDigit()
                    }
                    .font(.caption)
                }
            }
            Spacer(minLength: 0)
        }
        .padding(10)
        .background(outcome.verdict.color.opacity(0.1), in: RoundedRectangle(cornerRadius: 12, style: .continuous))
        .accessibilityElement(children: .combine)
    }
}
