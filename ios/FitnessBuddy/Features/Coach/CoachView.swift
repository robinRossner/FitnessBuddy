import SwiftUI

struct CoachView: View {
    @Environment(AppEnvironment.self) private var env
    @State private var path: [CoachRoute] = []
    @State private var showAsk: Bool
    @State private var recentOpen = false

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
                VStack(spacing: 16) {
                    CoachHeaderCard()
                    let groups = coach.groups
                    if !groups.pending.isEmpty {
                        sectionTitle("Needs your review", symbol: "checkmark.seal", count: groups.pending.count)
                        ForEach(groups.pending) { p in
                            NavigationLink(value: CoachRoute.proposal(p.id)) { PendingProposalCard(proposal: p) }
                                .buttonStyle(.plain)
                        }
                    }
                    if !groups.inFlight.isEmpty {
                        sectionTitle("In progress", symbol: "hourglass", count: nil)
                        ForEach(groups.inFlight) { p in
                            NavigationLink(value: CoachRoute.proposal(p.id)) { InFlightRow(proposal: p) }
                                .buttonStyle(.plain)
                        }
                    }
                    if groups.pending.isEmpty && groups.inFlight.isEmpty { emptyCard }
                    if !groups.recent.isEmpty { recentCard(groups.recent) }
                    historyLink
                    VStack(alignment: .leading, spacing: 8) {
                        SectionLabel(title: "Approval", symbol: "slider.horizontal.3")
                        Card { ApprovalModePicker() }
                    }
                    CoachFreshnessLabel(repository: coach)
                }
                .padding(.horizontal, 16)
                .padding(.vertical, 12)
            }
            .background(Color.screenBackground)
            .navigationTitle("Coach")
            .toolbar {
                ToolbarItem(placement: .topBarLeading) { SyncStatusBadge() }
                ToolbarItem(placement: .topBarTrailing) {
                    Button { showAsk = true } label: { Label("Ask coach", systemImage: "sparkles") }
                        .tint(CoachStyle.color)
                }
            }
            .navigationDestination(for: CoachRoute.self) { route in
                switch route {
                case .proposal(let id): ProposalDetailView(proposalId: id)
                case .history: CoachHistoryView()
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
    }

    private func consume(_ route: CoachRoute?) {
        guard let route else { return }
        env.coachRoute = nil
        if path.last != route { path.append(route) }
    }

    /// `-uiRoute history` / `-uiRoute proposal:<id>` open a screen at launch (screenshots).
    private func debugRoute() {
        #if DEBUG
        guard path.isEmpty, let raw = UserDefaults.standard.string(forKey: "uiRoute") else { return }
        if raw == "history" { path = [.history] }
        else if raw.hasPrefix("proposal:") { path = [.proposal(String(raw.dropFirst("proposal:".count)))] }
        #endif
    }

    private func sectionTitle(_ title: String, symbol: String, count: Int?) -> some View {
        HStack {
            SectionLabel(title: title, symbol: symbol, color: CoachStyle.color)
            if let count { Chip(text: "\(count)", color: CoachStyle.color) }
            Spacer()
        }
        .padding(.top, 4)
    }

    private var emptyCard: some View {
        Card {
            Label("Nothing to review", systemImage: "checkmark.circle")
                .font(.headline)
            Text("The coach only speaks up when a lift stalls, rules conflict, running stagnates, or once a week. You can also ask it a question with the sparkles button.")
                .font(.subheadline).foregroundStyle(.secondary)
        }
    }

    private func recentCard(_ recent: [CoachProposalDTO]) -> some View {
        Card {
            DisclosureGroup(isExpanded: $recentOpen) {
                VStack(spacing: 0) {
                    ForEach(recent.prefix(30)) { p in
                        NavigationLink(value: CoachRoute.proposal(p.id)) { RecentRow(proposal: p) }
                            .buttonStyle(.plain)
                        if p.id != recent.prefix(30).last?.id { Divider() }
                    }
                }
                .padding(.top, 8)
            } label: {
                HStack {
                    Label("Recent", systemImage: "clock.arrow.circlepath").font(.headline)
                    Spacer()
                    Text("\(recent.count)").font(.subheadline).foregroundStyle(.secondary)
                }
            }
            .tint(.secondary)
        }
    }

    private var historyLink: some View {
        NavigationLink(value: CoachRoute.history) {
            Card {
                HStack {
                    Image(systemName: "clock.arrow.2.circlepath").foregroundStyle(CoachStyle.color)
                    VStack(alignment: .leading, spacing: 2) {
                        Text("Config history").font(.headline).foregroundStyle(.primary)
                        Text(historySubtitle).font(.subheadline).foregroundStyle(.secondary)
                    }
                    Spacer()
                    Image(systemName: "chevron.right").font(.footnote.weight(.semibold)).foregroundStyle(.tertiary)
                }
            }
        }
        .buttonStyle(.plain)
    }

    private var historySubtitle: String {
        guard let v = env.coach.activeVersion else { return "Every version of your training settings, with roll back" }
        return "\(CoachLogic.versionLabel(v.version)) is active. Roll back any time."
    }
}

// MARK: - Header

/// Approval mode, whether the advisor is connected, dry-run, and the weekly call budget.
struct CoachHeaderCard: View {
    @Environment(AppEnvironment.self) private var env

    var body: some View {
        let settings = env.coach.settings
        Card(tint: CoachStyle.color) {
            HStack(alignment: .firstTextBaseline) {
                Label("Advisor", systemImage: "sparkles").font(.headline).foregroundStyle(CoachStyle.color)
                Spacer()
                if let settings {
                    if settings.dryRun { Chip(text: "Dry run", color: .indigo) }
                    Chip(text: settings.approvalMode.title, color: CoachStyle.color)
                }
            }
            if let settings {
                if !settings.apiKeyConfigured {
                    Label {
                        Text("Advisor not connected: add ANTHROPIC_API_KEY on the server")
                    } icon: {
                        Image(systemName: "exclamationmark.triangle.fill")
                    }
                    .font(.subheadline).foregroundStyle(.orange)
                } else {
                    Label("Connected", systemImage: "checkmark.circle.fill").font(.subheadline).foregroundStyle(.green)
                }
                if settings.dryRun {
                    Text("Dry run: requests are built but not sent to Claude, nothing is applied.")
                        .font(.footnote).foregroundStyle(.secondary)
                }
                HStack(spacing: 6) {
                    Image(systemName: "gauge.with.dots.needle.33percent").foregroundStyle(.secondary)
                    Text("Calls this week").foregroundStyle(.secondary)
                    Spacer()
                    Text("\(settings.callsThisWeek)/\(settings.maxCallsPerWeek)").font(.subheadline.weight(.semibold).monospacedDigit())
                }
                .font(.subheadline)
            } else if !env.coach.canFetch {
                Text("Add the backend URL and API token in Settings (Today tab) to use the coach.")
                    .font(.subheadline).foregroundStyle(.secondary)
            } else if let failure = env.coach.failure {
                Label(env.coach.isOffline ? "Offline. Nothing cached yet." : failure,
                      systemImage: env.coach.isOffline ? "icloud.slash" : "exclamationmark.triangle")
                    .font(.subheadline).foregroundStyle(.secondary)
            } else {
                HStack(spacing: 8) { ProgressView().controlSize(.small); Text("Loading…").foregroundStyle(.secondary) }
                    .font(.subheadline)
            }
        }
        .accessibilityElement(children: .combine)
    }
}

// MARK: - Rows

struct PendingProposalCard: View {
    let proposal: CoachProposalDTO
    private var validChanges: Int { proposal.changes.filter(CoachLogic.isSelectable).count }

    var body: some View {
        Card(tint: CoachStyle.color) {
            HStack(alignment: .top, spacing: 12) {
                TriggerBadge(type: proposal.trigger.type)
                VStack(alignment: .leading, spacing: 2) {
                    Text(proposal.trigger.title).font(.headline).foregroundStyle(.primary)
                        .fixedSize(horizontal: false, vertical: true)
                    if let date = proposal.createdAt {
                        Text(date.coachLabel).font(.caption).foregroundStyle(.secondary)
                    }
                }
                Spacer(minLength: 0)
                Image(systemName: "chevron.right").font(.footnote.weight(.semibold)).foregroundStyle(.tertiary)
            }
            if let diagnosis = proposal.diagnosis {
                ExpandableText(text: diagnosis, lines: 3)
            }
            HStack(spacing: 8) {
                ConfidencePill(confidence: proposal.confidence)
                Chip(text: "\(validChanges) change\(validChanges == 1 ? "" : "s")", color: CoachStyle.color)
                Spacer()
            }
        }
        .accessibilityElement(children: .combine)
        .accessibilityHint("Opens the proposal")
    }
}

/// Text clamped to a few lines with a "More" toggle (shown when the text is long enough to be clipped).
struct ExpandableText: View {
    let text: String
    var lines = 3
    @State private var expanded = false

    private var mayClip: Bool { text.count > lines * 40 }

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(text)
                .font(.subheadline).foregroundStyle(.secondary)
                .lineLimit(expanded ? nil : lines)
                .fixedSize(horizontal: false, vertical: true)
            if mayClip {
                Button(expanded ? "Less" : "More") { withAnimation(.snappy) { expanded.toggle() } }
                    .font(.footnote.weight(.semibold))
                    .foregroundStyle(CoachStyle.color)
                    .buttonStyle(.plain)
            }
        }
    }
}

struct InFlightRow: View {
    let proposal: CoachProposalDTO

    var body: some View {
        Card {
            HStack(spacing: 12) {
                TriggerBadge(type: proposal.trigger.type, size: 32)
                VStack(alignment: .leading, spacing: 2) {
                    Text(proposal.trigger.title).font(.subheadline.weight(.semibold)).foregroundStyle(.primary)
                    Text(statusLine).font(.footnote).foregroundStyle(.secondary)
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
        case .awaitingKey: "Waiting for the advisor key on the server"
        case .queued: "Queued"
        default: "Coach is thinking…"
        }
    }
}

struct RecentRow: View {
    let proposal: CoachProposalDTO

    var body: some View {
        HStack(spacing: 10) {
            Image(systemName: proposal.trigger.type.symbol).foregroundStyle(.secondary).frame(width: 22)
            VStack(alignment: .leading, spacing: 1) {
                Text(proposal.trigger.title).font(.subheadline).foregroundStyle(.primary).lineLimit(1)
                if let date = proposal.createdAt { Text(date.coachLabel).font(.caption).foregroundStyle(.secondary) }
            }
            Spacer(minLength: 8)
            CoachStatusChip(status: proposal.status)
            Image(systemName: "chevron.right").font(.caption.weight(.semibold)).foregroundStyle(.tertiary)
        }
        .padding(.vertical, 8)
        .contentShape(Rectangle())
        .accessibilityElement(children: .combine)
    }
}
