import FitnessCore
import SwiftUI

/// Coach tab: what needs a decision (compact Approve / Decline cards), this week's check-ins with a quiet
/// "Ask the coach" row, and the recent proposals. Approval mode, advisor status and config history sit behind the gear.
struct CoachView: View {
    @Environment(AppEnvironment.self) private var env
    @State private var path: [CoachScreen] = []
    @State private var showAsk: Bool
    /// Proposals decided from a card in this session: their result card stays until closed.
    @State private var outcomes: [String: CardOutcome] = [:]

    private let accent = Domain.coach.color
    private static let historyPreview = 5

    init() {
        #if DEBUG
        _showAsk = State(initialValue: UserDefaults.standard.string(forKey: "uiSheet") == "ask")
        #else
        _showAsk = State(initialValue: false)
        #endif
    }

    var body: some View {
        let coach = env.coach
        NavigationStack(path: $path) {
            ScrollView {
                VStack(alignment: .leading, spacing: Kit.groupSpacing) {
                    CoachStatusNotice()
                    reviewSection(coach.groups)
                    checkInsSection(coach.settings)
                    if !coach.groups.recent.isEmpty { historySection(coach.groups.recent) }
                    CoachFreshnessLabel(repository: coach)
                }
                .pagePadding()
            }
            .screenStyle()
            .navigationTitle("Coach")
            .toolbar {
                ToolbarItem(placement: .topBarLeading) { SyncStatusBadge() }
                ToolbarItem(placement: .topBarTrailing) {
                    NavigationLink(value: CoachScreen.settings) { Image(systemName: "gearshape") }
                        .tint(accent)
                        .accessibilityLabel("Coach Settings")
                }
            }
            .navigationDestination(for: CoachScreen.self) { screen in
                switch screen {
                case .proposal(let id): ProposalDetailView(proposalId: id)
                case .configHistory: CoachHistoryView()
                case .settings: CoachSettingsView()
                case .allProposals: CoachProposalListView()
                }
            }
            .refreshable {
                await env.sync.sync(.pullToRefresh)
                await coach.load(generation: env.sync.generation, force: true)
            }
            .task(id: env.sync.generation) { await coach.load(generation: env.sync.generation) }
            .sheet(isPresented: $showAsk) { AskCoachSheet(context: .general) }
            .onChange(of: env.coachRoute, initial: true) { _, route in consume(route) }
            .onAppear { debugRoute() }
        }
        .screenIdentifier(AXID.Screen.coach)
    }

    private func consume(_ route: CoachRoute?) {
        guard let route else { return }
        env.coachRoute = nil
        let screen = CoachScreen(route)
        if path.last != screen { path.append(screen) }
    }

    /// `-uiRoute history|settings|proposals|proposal:<id>` open a screen at launch (screenshots).
    private func debugRoute() {
        #if DEBUG
        guard path.isEmpty, let raw = UserDefaults.standard.string(forKey: "uiRoute") else { return }
        switch raw {
        case "history": path = [.configHistory]
        case "settings": path = [.settings]
        case "proposals": path = [.allProposals]
        default:
            if raw.hasPrefix("proposal:") { path = [.proposal(String(raw.dropFirst("proposal:".count)))] }
        }
        #endif
    }

    // MARK: To Review

    @ViewBuilder
    private func reviewSection(_ groups: CoachLogic.Groups) -> some View {
        let decided = outcomes.keys.sorted().compactMap { id -> DecidedProposal? in
            guard let p = env.coach.detail(id), !p.isPending, let outcome = outcomes[id] else { return nil }
            return DecidedProposal(proposal: p, outcome: outcome)
        }
        PageSection("To Review") {
            if groups.pending.isEmpty && groups.inFlight.isEmpty && decided.isEmpty {
                Card {
                    Label("Nothing to Review", systemImage: "checkmark.circle").font(.headline)
                    Text("The coach speaks up when a lift stalls, rules conflict or running stagnates, and once a week.")
                        .font(.subheadline).foregroundStyle(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
            ForEach(decided) { item in
                DecisionResultCard(proposal: item.proposal, outcome: item.outcome) {
                    withAnimation(.snappy) { _ = outcomes.removeValue(forKey: item.id) }
                }
            }
            ForEach(groups.pending) { p in
                PendingProposalCard(proposal: p) { outcome in
                    withAnimation(.snappy) { outcomes[p.id] = outcome }
                }
            }
            ForEach(groups.inFlight) { p in
                NavigationLink(value: CoachScreen.proposal(p.id)) { InFlightCard(proposal: p) }
                    .buttonStyle(.plain)
            }
        } trailing: {
            if groups.pending.count > 1 { Chip(text: "\(groups.pending.count)", color: accent) }
        }
    }

    // MARK: Check-ins

    private func checkInsSection(_ settings: CoachSettingsDTO?) -> some View {
        PageSection("Check-ins", info: "The coach checks in on its own: daily at 18:30 when something is open (not Sundays), and the weekly review on Sunday evening. Questions you ask don't count toward this.") {
            Card {
                CardHeader(title: "This Week", symbol: "bubble.left.and.text.bubble.right.fill", color: accent,
                           time: "Resets Monday")
                if let settings {
                    HStack(alignment: .firstTextBaseline) {
                        MetricText(value: "\(settings.callsThisWeek)",
                                   unit: settings.maxCallsPerWeek > 0 ? "of \(settings.maxCallsPerWeek)" : "check-ins",
                                   size: .title)
                        Spacer(minLength: 8)
                        Text(CoachCheckIns.remainingText(used: settings.callsThisWeek, limit: settings.maxCallsPerWeek))
                            .font(.subheadline).foregroundStyle(.secondary)
                    }
                    .accessibilityElement(children: .combine)
                    if let segments = CoachCheckIns.segments(used: settings.callsThisWeek, limit: settings.maxCallsPerWeek) {
                        CheckInGauge(segments: segments)
                    }
                } else {
                    Text("Usage appears once the coach settings have loaded.")
                        .font(.subheadline).foregroundStyle(.secondary)
                }
                Divider()
                Button { showAsk = true } label: {
                    HStack {
                        Label("Ask the Coach", systemImage: "bubble.left")
                        Spacer()
                        DisclosureChevron()
                    }
                    .font(.subheadline)
                    .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .foregroundStyle(accent)
                .accessibilityHint("Opens a sheet to ask the coach a question")
            }
        }
    }

    // MARK: History

    private func historySection(_ recent: [CoachProposalDTO]) -> some View {
        PageSection("History") {
            Card(padding: 0) {
                VStack(spacing: 0) {
                    let shown = Array(recent.prefix(Self.historyPreview))
                    ForEach(shown) { p in
                        CoachRowLink(screen: .proposal(p.id)) { CoachHistoryRow(proposal: p) }
                            .padding(.horizontal, 16)
                        if p.id != shown.last?.id || recent.count > shown.count { Divider().padding(.leading, 54) }
                    }
                    if recent.count > shown.count {
                        CoachRowLink(screen: .allProposals) {
                            Text("Show All (\(recent.count))").font(.body).foregroundStyle(.primary)
                        }
                        .padding(.horizontal, 16)
                    }
                }
            }
        }
    }
}

// MARK: - Status notice

/// Only shown when something is off: not set up, not connected, dry run, offline before the first load, loading.
struct CoachStatusNotice: View {
    @Environment(AppEnvironment.self) private var env

    var body: some View {
        let coach = env.coach
        if let settings = coach.settings {
            if !settings.isConnected {
                notice("Advisor Not Connected", symbol: "exclamationmark.triangle.fill", color: .orange,
                       text: "Add an advisor credential on the server to get proposals.")
            } else if settings.dryRun {
                notice("Dry Run", symbol: "testtube.2", color: .indigo,
                       text: "Requests are built but not sent to Claude; nothing is applied.")
            }
        } else if !coach.canFetch {
            LoadableContent(.notConfigured("Add the backend URL and API token in Settings to use the coach."), style: .notice)
        } else if let failure = coach.failure {
            LoadableContent(coach.isOffline ? .offline("Nothing cached yet.") : .failed(failure), style: .notice)
        } else if !coach.didFinishFirstLoad {
            LoadableContent(.loading(), style: .notice)
        }
    }

    private func notice(_ title: String, symbol: String, color: Color, text: String) -> some View {
        Card {
            CardHeader(title: title, symbol: symbol, color: color)
            Text(text).font(.subheadline).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
        }
        .accessibilityElement(children: .combine)
    }
}

// MARK: - Cards

enum CardOutcome: Equatable {
    case approved(version: String?)
    case declined
}

private struct DecidedProposal: Identifiable {
    let proposal: CoachProposalDTO
    let outcome: CardOutcome
    var id: String { proposal.id }
}

/// D's compact proposal card with E's "old → new" lines: headline, at most two changes, Approve / Decline, and one
/// row to the reasoning.
struct PendingProposalCard: View {
    @Environment(AppEnvironment.self) private var env
    let proposal: CoachProposalDTO
    let onDecided: (CardOutcome) -> Void

    @State private var acting: Action?
    @State private var error: String?

    private enum Action { case approving, declining }
    private static let shownLines = 2

    var body: some View {
        let lines = CoachChangeFormat.lines(for: proposal)
        let ids = CoachLogic.approvalIds(selection: CoachLogic.defaultSelection(of: proposal), proposal: proposal)
        let blocked = env.coach.writeBlockedHint
        Card {
            CardHeader(title: "Proposal", symbol: proposal.trigger.type.symbol, color: Domain.coach.color,
                       time: proposal.createdAt?.coachLabel)
            Text(proposal.trigger.title).font(.headline).fixedSize(horizontal: false, vertical: true)
            if !lines.isEmpty {
                VStack(alignment: .leading, spacing: 6) {
                    ForEach(lines.prefix(Self.shownLines)) { CoachChangeLine(line: $0) }
                    if lines.count > Self.shownLines {
                        Text("+\(lines.count - Self.shownLines) more").font(.footnote).foregroundStyle(.secondary)
                    }
                }
                .padding(.vertical, 2)
            }
            if let blocked { OfflineHint(text: blocked) }
            if let error {
                Label(error, systemImage: "exclamationmark.triangle.fill").font(.footnote).foregroundStyle(.orange)
                    .fixedSize(horizontal: false, vertical: true)
            }
            HStack(spacing: 10) {
                Button { approve(ids) } label: {
                    HStack(spacing: 6) {
                        if acting == .approving { ProgressView().tint(.white) }
                        Text("Approve")
                    }
                    .frame(maxWidth: .infinity)
                }
                .buttonStyle(.borderedProminent)
                .tint(Domain.coach.color)
                .disabled(ids.isEmpty || acting != nil || blocked != nil)
                Button { decline() } label: {
                    HStack(spacing: 6) {
                        if acting == .declining { ProgressView() }
                        Text("Decline")
                    }
                    .frame(maxWidth: .infinity)
                }
                .buttonStyle(.bordered)
                .tint(.secondary)
                .disabled(acting != nil || blocked != nil)
            }
            .controlSize(.large)
            .padding(.top, 2)
            CoachFooterLink(title: "Why and What Changes", screen: .proposal(proposal.id))
        }
    }

    private func approve(_ ids: [String]) {
        acting = .approving
        error = nil
        Task {
            do {
                let p = try await env.coach.approve(proposal.id, changeIds: ids)
                onDecided(.approved(version: p.appliedConfigVersion))
            } catch { self.error = CoachError.map(error).errorDescription }
            acting = nil
        }
    }

    private func decline() {
        acting = .declining
        error = nil
        Task {
            do {
                _ = try await env.coach.reject(proposal.id, note: nil)
                onDecided(.declined)
            } catch { self.error = CoachError.map(error).errorDescription }
            acting = nil
        }
    }
}

/// What happened to a proposal decided from its card. No undo: the API has none; an applied config can be rolled
/// back from Config History.
struct DecisionResultCard: View {
    let proposal: CoachProposalDTO
    let outcome: CardOutcome
    let close: () -> Void

    var body: some View {
        Card {
            HStack(spacing: 12) {
                Image(systemName: approved ? "checkmark.circle.fill" : "xmark.circle.fill")
                    .font(.title2)
                    .foregroundStyle(approved ? Color.green : Color.secondary)
                    .accessibilityHidden(true)
                VStack(alignment: .leading, spacing: 2) {
                    Text(title).font(.headline)
                    Text(subtitle).font(.subheadline).foregroundStyle(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
                Spacer(minLength: 8)
                Button(action: close) {
                    Image(systemName: "xmark").font(.footnote.weight(.semibold)).foregroundStyle(.secondary)
                        .frame(width: 28, height: 28)
                        .background(Color(.systemGray5), in: Circle())
                }
                .buttonStyle(.plain)
                .accessibilityLabel("Close")
            }
            CoachFooterLink(title: "Details", screen: .proposal(proposal.id))
        }
        .transition(.opacity)
    }

    private var approved: Bool { if case .approved = outcome { true } else { false } }

    private var title: String {
        guard approved else { return "Declined" }
        return proposal.status == .partiallyApplied ? "Partly Applied" : "Approved"
    }

    private var subtitle: String {
        switch outcome {
        case .declined: return "Your plan stays as it was."
        case .approved(let version):
            let v = version ?? proposal.appliedConfigVersion
            return (v.map { "Config \(CoachLogic.versionLabel($0)) is active. " } ?? "")
                + "Roll back any time in Coach Settings."
        }
    }
}

/// A queued / running / waiting-for-key proposal.
struct InFlightCard: View {
    let proposal: CoachProposalDTO

    var body: some View {
        Card {
            CardHeader(title: proposal.trigger.type == .ask ? "Your Question" : "Proposal",
                       symbol: proposal.trigger.type.symbol, color: Domain.coach.color,
                       time: proposal.createdAt?.coachLabel, chevron: true)
            HStack(spacing: 12) {
                VStack(alignment: .leading, spacing: 2) {
                    Text(proposal.trigger.title).font(.headline).foregroundStyle(.primary)
                        .fixedSize(horizontal: false, vertical: true)
                    Text(statusLine).font(.subheadline).foregroundStyle(.secondary)
                }
                Spacer(minLength: 0)
                if proposal.status.isInFlight { ProgressView().controlSize(.small) }
                else { Image(systemName: "key.fill").foregroundStyle(.orange) }
            }
        }
        .accessibilityElement(children: .combine)
    }

    private var statusLine: String {
        switch proposal.status {
        case .awaitingKey: "Waiting for the advisor credential on the server"
        case .queued: "Queued"
        default: "Coach is thinking…"
        }
    }
}

// MARK: - All proposals

/// Every decided or failed proposal, newest first.
struct CoachProposalListView: View {
    @Environment(AppEnvironment.self) private var env

    var body: some View {
        let recent = env.coach.groups.recent
        ScrollView {
            VStack(alignment: .leading, spacing: Kit.groupSpacing) {
                if recent.isEmpty {
                    Card {
                        Text("No proposals yet.").font(.subheadline).foregroundStyle(.secondary)
                    }
                } else {
                    Card(padding: 0) {
                        VStack(spacing: 0) {
                            ForEach(recent) { p in
                                CoachRowLink(screen: .proposal(p.id)) { CoachHistoryRow(proposal: p) }
                                    .padding(.horizontal, 16)
                                if p.id != recent.last?.id { Divider().padding(.leading, 54) }
                            }
                        }
                    }
                }
                CoachFreshnessLabel(repository: env.coach)
            }
            .pagePadding()
        }
        .screenStyle()
        .navigationTitle("History")
        .navigationBarTitleDisplayMode(.inline)
        .refreshable { await env.coach.refreshProposals() }
    }
}
