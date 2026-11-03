import FitnessCore
import SwiftUI

struct ProposalDetailView: View {
    @Environment(AppEnvironment.self) private var env
    let proposalId: String

    /// nil = nothing toggled yet, so every selectable change is on.
    @State private var selection: Set<String>?
    @State private var polling = false
    @State private var timedOut = false
    @State private var loadError: String?
    @State private var confirmApprove = false
    @State private var showReject = false
    @State private var rejectNote = ""
    @State private var acting: Action?
    @State private var actionError: String?

    private enum Action { case approving, rejecting }

    private var proposal: CoachProposalDTO? { env.coach.detail(proposalId) }

    var body: some View {
        Group {
            if let proposal {
                content(proposal)
            } else {
                loadingOrError
            }
        }
        .screenStyle()
        .navigationTitle("Proposal")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar(.hidden, for: .tabBar)
        .task(id: proposalId) { await load() }
        .onChange(of: env.sync.generation) { _, _ in Task { await refreshOnce() } }
        .sheet(isPresented: $showReject) { rejectSheet }
    }

    private var loadingOrError: some View {
        VStack(spacing: 12) {
            LoadableContent(loadError.map { env.coach.isOffline ? .offline($0) : .failed($0) } ?? .loading(), style: .screen,
                            retry: { Task { await load() } })
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    // MARK: Content

    private func content(_ p: CoachProposalDTO) -> some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 24) {
                headerCard(p)
                statusCard(p)
                if let diagnosis = p.diagnosis, !diagnosis.isEmpty {
                    PageSection("Why") {
                        Card { Text(diagnosis).font(.subheadline).fixedSize(horizontal: false, vertical: true) }
                    }
                }
                if !p.changes.isEmpty { changesSection(p) }
                if !p.scheduleChanges.isEmpty { scheduleSection(p) }
                if !p.sessionOverrides.isEmpty { overridesSection(p) }
                if let notes = p.notesForUser, !notes.isEmpty {
                    PageSection("Note for You") {
                        Card { Text(notes).font(.subheadline).fixedSize(horizontal: false, vertical: true) }
                    }
                }
                footnote(p)
            }
            .pagePadding()
            .padding(.bottom, p.isPending || p.canDismiss ? 8 : 0)
        }
        .refreshable { await refreshOnce() }
        .safeAreaInset(edge: .bottom) { if p.isPending { actionBar(p) } else if p.canDismiss { dismissBar } }
        .screenIdentifier(AXID.Coach.proposalDetail)
    }

    private func headerCard(_ p: CoachProposalDTO) -> some View {
        Card {
            CardHeader(title: p.status.title, symbol: p.trigger.type.symbol, color: Domain.coach.color,
                       time: p.createdAt?.coachLabel)
            Text(p.trigger.title).font(.title3.weight(.semibold)).fixedSize(horizontal: false, vertical: true)
            if let detail = p.trigger.detail, !detail.isEmpty, detail != p.question {
                Text(detail).font(.subheadline).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            }
            if let confidence = p.confidence, confidence != .unknown {
                HStack(spacing: 8) { ConfidencePill(confidence: confidence) }
            }
            if let q = p.question, !q.isEmpty {
                HStack(alignment: .top, spacing: 8) {
                    Image(systemName: "quote.opening").foregroundStyle(Domain.coach.color)
                    Text(q).font(.subheadline.italic()).fixedSize(horizontal: false, vertical: true)
                }
                .padding(10)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(Domain.coach.color.opacity(0.08), in: RoundedRectangle(cornerRadius: 12, style: .continuous))
            }
        }
    }

    private func statusNote(_ title: String, symbol: String, color: Color, text: String?) -> some View {
        Card {
            CardHeader(title: title, symbol: symbol, color: color)
            if let text {
                Text(text).font(.subheadline).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    @ViewBuilder private func statusCard(_ p: CoachProposalDTO) -> some View {
        switch p.status {
        case .queued, .running:
            Card {
                HStack(spacing: 12) {
                    if timedOut { Image(systemName: "hourglass").foregroundStyle(.orange) } else { ProgressView() }
                    VStack(alignment: .leading, spacing: 2) {
                        Text(timedOut ? "Still Working" : "Coach Is Thinking…").font(.headline)
                        Text(timedOut ? "This is taking longer than usual. Check back in a few minutes; the answer will be waiting here."
                                      : "This usually takes under a minute. You can leave this screen.")
                            .font(.subheadline).foregroundStyle(.secondary)
                    }
                }
                if timedOut {
                    Button("Check Again") { Task { await load() } }.buttonStyle(.bordered).tint(Domain.coach.color)
                }
            }
        case .awaitingKey:
            statusNote("Waiting for the Advisor", symbol: "key.fill", color: .orange,
                       text: "The coach can't answer yet. Add an advisor credential on the server; your question is saved and runs once it is there.")
        case .failed:
            statusNote("The Coach Couldn't Finish", symbol: "xmark.octagon.fill", color: .red,
                       text: p.error ?? "The request failed on the server. Nothing was changed.")
        case .dryRun:
            statusNote("Dry Run", symbol: "testtube.2", color: .indigo,
                       text: "The server built this request but did not send it to Claude, and nothing was applied.")
        case .applied, .partiallyApplied:
            Card {
                CardHeader(title: p.status == .applied ? "Applied" : "Partly Applied", symbol: "checkmark.seal.fill", color: .green)
                if let v = p.appliedConfigVersion {
                    Text("Config \(CoachLogic.versionLabel(v)) is active. Summary and Progress use the re-planned sessions.")
                        .font(.subheadline).foregroundStyle(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
                CoachFooterLink(title: "Config History", screen: .configHistory)
            }
        case .rejected:
            statusNote("Declined", symbol: "hand.thumbsdown.fill", color: .secondary, text: "Nothing was changed.")
        case .pending, .unknown:
            EmptyView()
        }
    }

    // MARK: Changes

    private func selectedText(_ selected: Int, of total: Int) -> some View {
        Text("\(selected) of \(total) selected").font(.subheadline).foregroundStyle(.secondary)
    }

    private func changesSection(_ p: CoachProposalDTO) -> some View {
        let reviewing = p.isPending
        let effective = selection ?? CoachLogic.defaultSelection(of: p)
        return PageSection("Changes") {
            Card {
                ForEach(Array(p.changes.enumerated()), id: \.element.id) { index, change in
                    if index > 0 { Divider() }
                    changeRow(change, reviewing: reviewing, selected: effective.contains(change.id), proposal: p)
                }
            }
        } trailing: {
            if reviewing {
                selectedText(CoachLogic.approvalIds(selection: effective, changes: p.changes).count,
                             of: p.changes.filter { CoachLogic.isSelectable($0) }.count)
            }
        }
    }

    private func changeRow(_ c: CoachChangeDTO, reviewing: Bool, selected: Bool, proposal p: CoachProposalDTO) -> some View {
        let parts = CoachChangeFormat.parts(from: c.from, to: c.to, path: c.param)
        return HStack(alignment: .top, spacing: 12) {
            VStack(alignment: .leading, spacing: 6) {
                HStack(spacing: 8) {
                    Text(CoachChangeFormat.title(label: c.label, param: c.param))
                        .font(.subheadline.weight(.semibold)).fixedSize(horizontal: false, vertical: true)
                    RiskPill(risk: c.risk)
                }
                CoachValueChange(old: parts.old, new: parts.new)
                    .font(.body)
                    .fixedSize(horizontal: false, vertical: true)
                if !c.reason.isEmpty {
                    Text(c.reason).font(.footnote).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                }
                invalidOrStatus(valid: c.valid, error: c.validationError, status: c.status, reviewing: reviewing)
            }
            Spacer(minLength: 0)
            if reviewing && c.valid { toggle(c.id, label: c.label, selected: selected, proposal: p) }
        }
        .opacity(c.valid ? 1 : 0.55)
        .accessibilityElement(children: .contain)
    }

    @ViewBuilder
    private func invalidOrStatus(valid: Bool, error: String?, status: CoachChangeStatus, reviewing: Bool) -> some View {
        if !valid {
            Label {
                Text((error ?? "Not allowed") + ". Rejected automatically.")
            } icon: { Image(systemName: "exclamationmark.triangle.fill") }
            .font(.footnote).foregroundStyle(.red)
            .fixedSize(horizontal: false, vertical: true)
        } else if !reviewing, status == .applied || status == .rejected {
            Chip(text: status == .applied ? "Applied" : "Not applied", color: status == .applied ? .green : .gray)
        }
    }

    private func toggle(_ id: String, label: String, selected: Bool, proposal p: CoachProposalDTO) -> some View {
        Toggle("Apply \(label)", isOn: Binding(
            get: { selected },
            set: { _ in selection = CoachLogic.toggled(id, in: selection ?? CoachLogic.defaultSelection(of: p), proposal: p) }
        ))
        .labelsHidden()
        .tint(Domain.coach.color)
    }

    private func scheduleSection(_ p: CoachProposalDTO) -> some View {
        let reviewing = p.isPending
        let effective = selection ?? CoachLogic.defaultSelection(of: p)
        return PageSection("Schedule") {
            Card {
                ForEach(Array(p.scheduleChanges.enumerated()), id: \.element.id) { index, change in
                    if index > 0 { Divider() }
                    scheduleRow(change, reviewing: reviewing, selected: effective.contains(change.id), proposal: p)
                }
            }
        } trailing: {
            if reviewing {
                selectedText(CoachLogic.approvalIds(selection: effective, changes: p.scheduleChanges).count,
                             of: p.scheduleChanges.filter { CoachLogic.isSelectable($0) }.count)
            }
        }
    }

    private func scheduleRow(_ c: CoachScheduleChangeDTO, reviewing: Bool, selected: Bool, proposal p: CoachProposalDTO) -> some View {
        HStack(alignment: .top, spacing: 12) {
            Image(systemName: c.action.symbol)
                .font(.title3).foregroundStyle(Domain.coach.color).frame(width: 24)
                .accessibilityHidden(true)
            VStack(alignment: .leading, spacing: 6) {
                HStack(spacing: 8) {
                    Text(c.label).font(.subheadline.weight(.semibold)).fixedSize(horizontal: false, vertical: true)
                    RiskPill(risk: c.risk)
                }
                if !c.reason.isEmpty {
                    Text(c.reason).font(.footnote).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                }
                invalidOrStatus(valid: c.valid, error: c.validationError, status: c.status, reviewing: reviewing)
            }
            Spacer(minLength: 0)
            if reviewing && c.valid { toggle(c.id, label: c.label, selected: selected, proposal: p) }
        }
        .opacity(c.valid ? 1 : 0.55)
        .accessibilityElement(children: .contain)
    }

    private func overridesSection(_ p: CoachProposalDTO) -> some View {
        PageSection("Planned Sessions") {
            Card {
                ForEach(Array(p.sessionOverrides.enumerated()), id: \.element.id) { index, o in
                    if index > 0 { Divider() }
                    VStack(alignment: .leading, spacing: 2) {
                        HStack(spacing: 6) {
                            if let date = o.date { Text(DayLabel.short(date)).font(.caption.weight(.semibold)).foregroundStyle(.secondary) }
                            if let session = o.session { Chip(text: session.replacingOccurrences(of: "_", with: " ").capitalized, color: Domain.forKind(session).color) }
                        }
                        Text(o.change).font(.subheadline).fixedSize(horizontal: false, vertical: true)
                        if let reason = o.reason, !reason.isEmpty {
                            Text(reason).font(.footnote).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                        }
                    }
                }
            }
        }
    }

    private func footnote(_ p: CoachProposalDTO) -> some View {
        VStack(spacing: 2) {
            if let cost = p.costUsd {
                Text("Cost \(cost.formatted(.currency(code: "USD").precision(.fractionLength(cost < 0.1 ? 3 : 2))))")
            }
            if let error = loadError { Text("Couldn't refresh: \(error)").foregroundStyle(.orange) }
        }
        .font(.caption)
        .foregroundStyle(.secondary)
        .frame(maxWidth: .infinity)
    }

    // MARK: Actions

    private func actionBar(_ p: CoachProposalDTO) -> some View {
        let effective = selection ?? CoachLogic.defaultSelection(of: p)
        let ids = CoachLogic.approvalIds(selection: effective, proposal: p)
        let blocked = env.coach.writeBlockedHint
        let next = CoachLogic.nextVersion(after: env.coach.activeVersion?.version)
        return VStack(spacing: 8) {
            if let blocked { OfflineHint(text: blocked) }
            if let actionError {
                Label(actionError, systemImage: "exclamationmark.triangle.fill").font(.footnote).foregroundStyle(.orange)
            }
            HStack(spacing: 12) {
                Button { showReject = true } label: {
                    Text("Decline").frame(maxWidth: .infinity).padding(.vertical, 6)
                }
                .buttonStyle(.bordered)
                .tint(.secondary)
                .disabled(acting != nil || blocked != nil)
                Button { confirmApprove = true } label: {
                    HStack(spacing: 6) {
                        if acting == .approving { ProgressView().tint(.white) }
                        Text(ids.isEmpty ? "Nothing selected" : "Approve (\(ids.count))").lineLimit(1)
                    }
                    .frame(maxWidth: .infinity).padding(.vertical, 6)
                }
                .buttonStyle(.borderedProminent)
                .tint(Domain.coach.color)
                .disabled(ids.isEmpty || acting != nil || blocked != nil)
                .accessibilityIdentifier(AXID.Coach.detailApprove)
            }
        }
        .padding(.horizontal, 16).padding(.vertical, 10)
        .background(.bar)
        .confirmationDialog("Approve \(ids.count) change\(ids.count == 1 ? "" : "s")?", isPresented: $confirmApprove, titleVisibility: .visible) {
            Button("Approve") { approve(ids) }
            Button("Cancel", role: .cancel) {}
        } message: {
            Text((next.map { "Creates config \($0). " } ?? "Creates a new config version. ") + "You can roll back in Coach Settings.")
        }
    }

    /// awaiting_key / failed / dry_run: nothing to approve, only clear it.
    private var dismissBar: some View {
        let blocked = env.coach.writeBlockedHint
        return VStack(spacing: 8) {
            if let blocked { OfflineHint(text: blocked) }
            if let actionError {
                Label(actionError, systemImage: "exclamationmark.triangle.fill").font(.footnote).foregroundStyle(.orange)
            }
            Button { showReject = true } label: {
                HStack(spacing: 6) {
                    if acting == .rejecting { ProgressView() }
                    Text("Dismiss").frame(maxWidth: .infinity).padding(.vertical, 6)
                }
            }
            .buttonStyle(.bordered)
            .tint(.red)
            .disabled(acting != nil || blocked != nil)
        }
        .padding(.horizontal, 16).padding(.vertical, 10)
        .background(.bar)
    }

    private var rejectSheet: some View {
        NavigationStack {
            Form {
                Section {
                    TextField("Why? (optional)", text: $rejectNote, axis: .vertical).lineLimit(3...6)
                } footer: {
                    Text("The note helps the coach next time. Nothing is changed.")
                }
            }
            .navigationTitle(proposal?.canDismiss == true ? "Dismiss Proposal" : "Decline Proposal")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Cancel") { showReject = false } }
                ToolbarItem(placement: .confirmationAction) {
                    Button(proposal?.canDismiss == true ? "Dismiss" : "Decline", role: .destructive) { showReject = false; reject() }
                        .disabled(env.coach.writeBlockedHint != nil)
                }
            }
        }
        .presentationDetents([.medium])
    }

    private func approve(_ ids: [String]) {
        acting = .approving
        actionError = nil
        Task {
            do { _ = try await env.coach.approve(proposalId, changeIds: ids); selection = nil }
            catch { actionError = CoachError.map(error).errorDescription }
            acting = nil
        }
    }

    private func reject() {
        acting = .rejecting
        actionError = nil
        let note = rejectNote
        Task {
            do { _ = try await env.coach.reject(proposalId, note: note); rejectNote = "" }
            catch { actionError = CoachError.map(error).errorDescription }
            acting = nil
        }
    }

    // MARK: Loading

    /// Cached copy first, then the server, then (for a queued / running proposal) poll while the screen is visible.
    private func load() async {
        timedOut = false
        await env.coach.loadCachedDetail(proposalId)
        await refreshOnce()
        guard let p = proposal, CoachLogic.shouldPoll(p.status), env.coach.canFetch else { return }
        polling = true
        let outcome = await env.coach.poll(proposalId)
        polling = false
        timedOut = outcome == .timedOut
    }

    private func refreshOnce() async {
        do { try await env.coach.fetchProposal(proposalId); loadError = nil }
        catch { loadError = CoachError.map(error).errorDescription }
    }
}
