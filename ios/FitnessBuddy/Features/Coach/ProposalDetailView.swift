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
        .background(Color.screenBackground)
        .navigationTitle("Proposal")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar(.hidden, for: .tabBar)
        .task(id: proposalId) { await load() }
        .onChange(of: env.sync.generation) { _, _ in Task { await refreshOnce() } }
        .sheet(isPresented: $showReject) { rejectSheet }
    }

    private var loadingOrError: some View {
        VStack(spacing: 12) {
            if let loadError {
                ContentUnavailableView {
                    Label(env.coach.isOffline ? "Offline" : "Couldn't load", systemImage: env.coach.isOffline ? "icloud.slash" : "exclamationmark.triangle")
                } description: { Text(loadError) } actions: {
                    Button("Try again") { Task { await load() } }
                }
            } else {
                ProgressView()
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    // MARK: Content

    private func content(_ p: CoachProposalDTO) -> some View {
        ScrollView {
            VStack(spacing: 16) {
                headerCard(p)
                statusCard(p)
                if let diagnosis = p.diagnosis, !diagnosis.isEmpty {
                    Card {
                        SectionLabel(title: "Diagnosis", symbol: "stethoscope", color: CoachStyle.color)
                        Text(diagnosis).font(.subheadline).fixedSize(horizontal: false, vertical: true)
                    }
                }
                if !p.changes.isEmpty { changesCard(p) }
                if !p.scheduleChanges.isEmpty { scheduleCard(p) }
                if !p.sessionOverrides.isEmpty { overridesCard(p) }
                if let notes = p.notesForUser, !notes.isEmpty {
                    Card {
                        SectionLabel(title: "Note for you", symbol: "note.text", color: CoachStyle.color)
                        Text(notes).font(.subheadline).fixedSize(horizontal: false, vertical: true)
                    }
                }
                footnote(p)
            }
            .padding(.horizontal, 16)
            .padding(.vertical, 12)
            .padding(.bottom, p.isPending || p.canDismiss ? 8 : 0)
        }
        .refreshable { await refreshOnce() }
        .safeAreaInset(edge: .bottom) { if p.isPending { actionBar(p) } else if p.canDismiss { dismissBar } }
    }

    private func headerCard(_ p: CoachProposalDTO) -> some View {
        Card(tint: CoachStyle.color) {
            HStack(alignment: .top, spacing: 12) {
                TriggerBadge(type: p.trigger.type, size: 40)
                VStack(alignment: .leading, spacing: 3) {
                    Text(p.trigger.title).font(.title3.weight(.semibold)).fixedSize(horizontal: false, vertical: true)
                    if let detail = p.trigger.detail, !detail.isEmpty, detail != p.question {
                        Text(detail).font(.subheadline).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                    }
                }
            }
            HStack(spacing: 8) {
                CoachStatusChip(status: p.status)
                ConfidencePill(confidence: p.confidence)
                Spacer()
                if let date = p.createdAt { Text(date.coachLabel).font(.caption).foregroundStyle(.secondary) }
            }
            if let q = p.question, !q.isEmpty {
                HStack(alignment: .top, spacing: 8) {
                    Image(systemName: "quote.opening").foregroundStyle(CoachStyle.color)
                    Text(q).font(.subheadline.italic()).fixedSize(horizontal: false, vertical: true)
                }
                .padding(10)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(CoachStyle.color.opacity(0.08), in: RoundedRectangle(cornerRadius: 12, style: .continuous))
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
                        Text(timedOut ? "Still working" : "Coach is thinking…").font(.headline)
                        Text(timedOut ? "This is taking longer than usual. Check back in a few minutes; the answer will be waiting here."
                                      : "This usually takes under a minute. You can leave this screen.")
                            .font(.subheadline).foregroundStyle(.secondary)
                    }
                }
                if timedOut {
                    Button("Check again") { Task { await load() } }.buttonStyle(.bordered).tint(CoachStyle.color)
                }
            }
        case .awaitingKey:
            Card(tint: .orange) {
                Label("Waiting for the advisor key", systemImage: "key.fill").font(.headline).foregroundStyle(.orange)
                Text("The coach can't answer yet. Add ANTHROPIC_API_KEY on the server; your question is saved and runs once the key is there.")
                    .font(.subheadline).foregroundStyle(.secondary)
            }
        case .failed:
            Card(tint: .red) {
                Label("The coach couldn't finish", systemImage: "xmark.octagon.fill").font(.headline).foregroundStyle(.red)
                Text(p.error ?? "The request failed on the server. Nothing was changed.")
                    .font(.subheadline).foregroundStyle(.secondary)
            }
        case .dryRun:
            Card(tint: .indigo) {
                Label("Dry run", systemImage: "testtube.2").font(.headline).foregroundStyle(.indigo)
                Text("The server built this request but did not send it to Claude, and nothing was applied.")
                    .font(.subheadline).foregroundStyle(.secondary)
            }
        case .applied, .partiallyApplied:
            Card(tint: .green) {
                Label(p.status == .applied ? "Applied" : "Partly applied", systemImage: "checkmark.seal.fill")
                    .font(.headline).foregroundStyle(.green)
                if let v = p.appliedConfigVersion {
                    Text("New config \(CoachLogic.versionLabel(v)) is active. Today and Progress use the re-planned sessions.")
                        .font(.subheadline).foregroundStyle(.secondary)
                }
                NavigationLink(value: CoachRoute.history) {
                    Label("Open history", systemImage: "clock.arrow.2.circlepath").font(.subheadline.weight(.semibold))
                }
                .tint(CoachStyle.color)
            }
        case .rejected:
            Card {
                Label("Rejected", systemImage: "hand.thumbsdown.fill").font(.headline).foregroundStyle(.secondary)
                Text("Nothing was changed.").font(.subheadline).foregroundStyle(.secondary)
            }
        case .pending, .unknown:
            EmptyView()
        }
    }

    // MARK: Changes

    private func changesCard(_ p: CoachProposalDTO) -> some View {
        let reviewing = p.isPending
        let effective = selection ?? CoachLogic.defaultSelection(of: p)
        return Card {
            HStack {
                SectionLabel(title: "Parameter changes", symbol: "slider.horizontal.3", color: CoachStyle.color)
                Spacer()
                if reviewing {
                    Text("\(CoachLogic.approvalIds(selection: effective, changes: p.changes).count) of \(p.changes.filter { CoachLogic.isSelectable($0) }.count) selected")
                        .font(.caption).foregroundStyle(.secondary)
                }
            }
            VStack(spacing: 0) {
                ForEach(Array(p.changes.enumerated()), id: \.element.id) { index, change in
                    if index > 0 { Divider().padding(.vertical, 10) }
                    changeRow(change, reviewing: reviewing, selected: effective.contains(change.id), proposal: p)
                }
            }
        }
    }

    private func changeRow(_ c: CoachChangeDTO, reviewing: Bool, selected: Bool, proposal p: CoachProposalDTO) -> some View {
        HStack(alignment: .top, spacing: 12) {
            VStack(alignment: .leading, spacing: 6) {
                HStack(spacing: 8) {
                    Text(c.label).font(.subheadline.weight(.semibold)).fixedSize(horizontal: false, vertical: true)
                    RiskPill(risk: c.risk)
                }
                ChangeValueText(text: c.changeText)
                if !c.reason.isEmpty {
                    Text(c.reason).font(.footnote).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                }
                if !c.valid {
                    Label {
                        Text((c.validationError ?? "Not allowed") + ". Rejected automatically.")
                    } icon: { Image(systemName: "exclamationmark.triangle.fill") }
                    .font(.footnote).foregroundStyle(.red)
                    .fixedSize(horizontal: false, vertical: true)
                } else if !reviewing, c.status == .applied || c.status == .rejected {
                    Chip(text: c.status == .applied ? "Applied" : "Not applied", color: c.status == .applied ? .green : .gray)
                }
            }
            Spacer(minLength: 0)
            if reviewing && c.valid {
                Toggle("Apply \(c.label)", isOn: Binding(
                    get: { selected },
                    set: { _ in selection = CoachLogic.toggled(c.id, in: selection ?? CoachLogic.defaultSelection(of: p), proposal: p) }
                ))
                .labelsHidden()
                .tint(CoachStyle.color)
            }
        }
        .opacity(c.valid ? 1 : 0.55)
        .accessibilityElement(children: .contain)
    }

    private func scheduleCard(_ p: CoachProposalDTO) -> some View {
        let reviewing = p.isPending
        let effective = selection ?? CoachLogic.defaultSelection(of: p)
        return Card {
            HStack {
                SectionLabel(title: "Schedule changes", symbol: "calendar.badge.clock", color: CoachStyle.color)
                Spacer()
                if reviewing {
                    Text("\(CoachLogic.approvalIds(selection: effective, changes: p.scheduleChanges).count) of \(p.scheduleChanges.filter { CoachLogic.isSelectable($0) }.count) selected")
                        .font(.caption).foregroundStyle(.secondary)
                }
            }
            VStack(spacing: 0) {
                ForEach(Array(p.scheduleChanges.enumerated()), id: \.element.id) { index, change in
                    if index > 0 { Divider().padding(.vertical, 10) }
                    scheduleRow(change, reviewing: reviewing, selected: effective.contains(change.id), proposal: p)
                }
            }
        }
    }

    private func scheduleRow(_ c: CoachScheduleChangeDTO, reviewing: Bool, selected: Bool, proposal p: CoachProposalDTO) -> some View {
        HStack(alignment: .top, spacing: 12) {
            Image(systemName: c.action.symbol)
                .font(.title3).foregroundStyle(CoachStyle.color).frame(width: 24)
                .accessibilityHidden(true)
            VStack(alignment: .leading, spacing: 6) {
                HStack(spacing: 8) {
                    Text(c.label).font(.subheadline.weight(.semibold)).fixedSize(horizontal: false, vertical: true)
                    RiskPill(risk: c.risk)
                }
                if !c.reason.isEmpty {
                    Text(c.reason).font(.footnote).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                }
                if !c.valid {
                    Label {
                        Text((c.validationError ?? "Not allowed") + ". Rejected automatically.")
                    } icon: { Image(systemName: "exclamationmark.triangle.fill") }
                    .font(.footnote).foregroundStyle(.red)
                    .fixedSize(horizontal: false, vertical: true)
                } else if !reviewing, c.status == .applied || c.status == .rejected {
                    Chip(text: c.status == .applied ? "Applied" : "Not applied", color: c.status == .applied ? .green : .gray)
                }
            }
            Spacer(minLength: 0)
            if reviewing && c.valid {
                Toggle("Apply \(c.label)", isOn: Binding(
                    get: { selected },
                    set: { _ in selection = CoachLogic.toggled(c.id, in: selection ?? CoachLogic.defaultSelection(of: p), proposal: p) }
                ))
                .labelsHidden()
                .tint(CoachStyle.color)
            }
        }
        .opacity(c.valid ? 1 : 0.55)
        .accessibilityElement(children: .contain)
    }

    private func overridesCard(_ p: CoachProposalDTO) -> some View {
        Card {
            SectionLabel(title: "Planned sessions", symbol: "calendar.badge.clock", color: CoachStyle.color)
            ForEach(p.sessionOverrides) { o in
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
                Button(role: .destructive) { showReject = true } label: {
                    Text("Reject").frame(maxWidth: .infinity).padding(.vertical, 6)
                }
                .buttonStyle(.bordered)
                .tint(.red)
                .disabled(acting != nil || blocked != nil)
                Button { confirmApprove = true } label: {
                    HStack(spacing: 6) {
                        if acting == .approving { ProgressView().tint(.white) }
                        Text(ids.isEmpty ? "Nothing selected" : "Approve (\(ids.count))").lineLimit(1)
                    }
                    .frame(maxWidth: .infinity).padding(.vertical, 6)
                }
                .buttonStyle(.borderedProminent)
                .tint(CoachStyle.color)
                .disabled(ids.isEmpty || acting != nil || blocked != nil)
            }
        }
        .padding(.horizontal, 16).padding(.vertical, 10)
        .background(.bar)
        .confirmationDialog("Approve \(ids.count) change\(ids.count == 1 ? "" : "s")?", isPresented: $confirmApprove, titleVisibility: .visible) {
            Button("Approve") { approve(ids) }
            Button("Cancel", role: .cancel) {}
        } message: {
            Text((next.map { "Creates config \($0). " } ?? "Creates a new config version. ") + "You can roll back in History.")
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
            .navigationTitle(proposal?.canDismiss == true ? "Dismiss proposal" : "Reject proposal")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Cancel") { showReject = false } }
                ToolbarItem(placement: .confirmationAction) {
                    Button(proposal?.canDismiss == true ? "Dismiss" : "Reject", role: .destructive) { showReject = false; reject() }
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
        env.coach.loadCachedDetail(proposalId)
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
