import SwiftData
import SwiftUI

struct TodayView: View {
    @Environment(AppEnvironment.self) private var env
    @Query(sort: \CachedPlannedSession.date) private var planned: [CachedPlannedSession]
    @Query private var states: [SyncState]
    @Query(filter: #Predicate<LocalSession> { $0.deletedAt == nil }, sort: \LocalSession.startedAt, order: .reverse)
    private var sessions: [LocalSession]
    @State private var showSettings: Bool
    private let openSheet: String?

    init() {
        #if DEBUG
        openSheet = UserDefaults.standard.string(forKey: "uiSheet")
        #else
        openSheet = nil
        #endif
        _showSettings = State(initialValue: openSheet == "settings")
    }

    private var today: String { AppClock.todayString }
    private var todayItems: [CachedPlannedSession] { planned.filter { $0.date == today } }
    private var coachPending: Int { states.first?.coachPendingCount ?? 0 }
    private var activeSession: LocalSession? { sessions.first { $0.isActive } }

    private var readinessSummary: ReadinessSummary? {
        let state = states.first.flatMap { $0.readinessDate == today ? $0 : nil }
        let legacy = state?.readiness ?? todayItems.compactMap { $0.item?.readiness }.first { $0.color != nil }
        return ReadinessSummary.make(today: state?.readinessToday, legacy: legacy, day: nil)
    }

    private var runDays: Set<String> { Set((env.stats.resource(StatsEndpoints.recentRuns()).value ?? []).compactMap(\.day)) }

    private var weekRuns: WeekRunsModel {
        WeekRunsModel.build(
            days: AppClock.weekDays(containing: AppClock.now),
            items: planned.compactMap(\.item),
            runs: env.stats.resource(StatsEndpoints.recentRuns()).value ?? [],
            finished: sessions.filter { $0.endedAt != nil && $0.deletedAt == nil }
                .map { FinishedDayRef(plannedId: $0.plannedSessionId, day: AppClock.dayString($0.startedAt), kind: $0.kind) },
            today: today)
    }

    private var hasLiftingToday: Bool { todayItems.contains { Domain.forKind($0.kind) == .lifting } }

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(spacing: 16) {
                    if CoachLogic.bannerVisible(pendingCount: coachPending) {
                        CoachBanner(pendingCount: coachPending, latestId: states.first?.coachLatestPendingId)
                    }
                    ReadinessCard(summary: readinessSummary, startOpen: openSheet == "readiness")
                    if todayItems.isEmpty {
                        EmptyPlanCard(isConfigured: env.settings.isConfigured) { showSettings = true }
                    } else {
                        ForEach(todayItems, id: \.id) { row in
                            if let item = row.item {
                                SessionCard(row: row, item: item, activeSession: activeSession,
                                            completed: completedSession(for: row), openDetail: openSheet == "run")
                            }
                        }
                    }
                    if TodayLogic.showsResumeCard(hasActiveWorkout: activeSession != nil, hasLiftingToday: hasLiftingToday) {
                        ResumeWorkoutCard()
                    }
                    WeekRunsCard(model: weekRuns, today: today, openFirstAdaptive: openSheet == "weekrun")
                    WeekStrip(planned: planned, sessions: sessions, runDays: runDays, today: today)
                }
                .padding(.horizontal, 16)
                .padding(.vertical, 12)
            }
            .background(Color.screenBackground)
            .navigationTitle("Today")
            .toolbar {
                ToolbarItem(placement: .topBarLeading) { SyncStatusBadge() }
                ToolbarItem(placement: .topBarTrailing) {
                    Button { showSettings = true } label: { Image(systemName: "gearshape") }
                        .accessibilityLabel("Settings")
                }
            }
            .refreshable { await env.sync.sync(.pullToRefresh) }
            .loadsStats([env.stats.resource(StatsEndpoints.recentRuns())])
            .sheet(isPresented: $showSettings) { SettingsView() }
        }
    }

    private func completedSession(for row: CachedPlannedSession) -> LocalSession? {
        sessions.first { $0.endedAt != nil && ($0.plannedSessionId == row.id) }
    }
}

struct EmptyPlanCard: View {
    let isConfigured: Bool
    let openSettings: () -> Void

    var body: some View {
        Card {
            Label(isConfigured ? "No plan cached yet" : "Connect your backend", systemImage: "icloud.and.arrow.down")
                .font(.headline)
            Text(isConfigured ? "Pull down to sync. Once a plan has been fetched it stays available offline."
                              : "Add the backend URL and API token in Settings to load your weekly plan.")
                .font(.subheadline).foregroundStyle(.secondary)
            if !isConfigured {
                Button("Open Settings", action: openSettings).buttonStyle(.borderedProminent)
            }
        }
    }
}

// MARK: - Session card

struct SessionCard: View {
    @Environment(AppEnvironment.self) private var env
    let row: CachedPlannedSession
    let item: PlanItemDTO
    let activeSession: LocalSession?
    let completed: LocalSession?
    @State private var showRunDetail: Bool

    init(row: CachedPlannedSession, item: PlanItemDTO, activeSession: LocalSession?, completed: LocalSession?, openDetail: Bool = false) {
        self.row = row; self.item = item; self.activeSession = activeSession; self.completed = completed
        _showRunDetail = State(initialValue: openDetail && ["easy_run", "long_run", "intervals"].contains(item.session?.type ?? item.kind))
    }

    private var isRun: Bool { ["easy_run", "long_run", "intervals"].contains(type) }

    private var domain: Domain { Domain.forKind(item.kind) }
    private var type: String { item.session?.type ?? item.kind }

    var body: some View {
        if isRun {
            Button { showRunDetail = true } label: { card }
                .buttonStyle(.plain)
                .accessibilityHint("Shows the full session")
                .sheet(isPresented: $showRunDetail) { RunDetailSheet(item: item) }
        } else {
            card
        }
    }

    private var card: some View {
        Card(tint: domain.color) {
            header
            if !item.adjustments.isEmpty { AdjustmentNote(lines: item.adjustments, level: item.readiness?.level ?? .unknown) }
            switch type {
            case "lifting": LiftingBody(session: item.session)
            case "intervals": IntervalsBody(session: item.session)
            case "easy_run", "long_run": EasyRunBody(session: item.session)
            default: RestBody(notes: item.notes, optional: item.session?.optional ?? false)
            }
            if type == "lifting" { startButton }
            if type != "lifting", type != "rest", !item.notes.isEmpty {
                ForEach(item.notes, id: \.self) { Text($0).font(.footnote).foregroundStyle(.secondary) }
            }
        }
    }

    private var header: some View {
        HStack(alignment: .firstTextBaseline) {
            Image(systemName: PlanFormat.symbol(forKind: item.kind)).foregroundStyle(domain.color)
                .accessibilityHidden(true)
            Text(item.title ?? PlanFormat.runTitle(type)).font(.title3.weight(.semibold))
                .fixedSize(horizontal: false, vertical: true)
            Spacer(minLength: 8)
            if let gym = item.session?.gym, type == "lifting" { Chip(text: gym, color: domain.color) }
            if item.session?.deload?.active == true { Chip(text: "Deload", color: .purple) }
            if isRun {
                Image(systemName: "chevron.right").font(.footnote.weight(.semibold)).foregroundStyle(.tertiary)
                    .accessibilityHidden(true)
            }
        }
    }

    /// Starting a workout lives in the Workout tab; here only "Done" and, for a running workout, "Resume".
    @ViewBuilder private var startButton: some View {
        switch TodayLogic.liftCardAction(completed: completed != nil, hasActiveWorkout: activeSession != nil) {
        case .done:
            if let completed {
                Label("Done · \(Fmt.longDuration((completed.endedAt ?? .now).timeIntervalSince(completed.startedAt)))",
                      systemImage: "checkmark.circle.fill")
                    .font(.subheadline.weight(.semibold)).foregroundStyle(.green)
            }
        case .resume:
            Button { env.selectedTab = .workout } label: {
                Label("Resume workout", systemImage: "play.fill").frame(maxWidth: .infinity, minHeight: 34)
            }
            .buttonStyle(.borderedProminent).tint(domain.color)
        case .none:
            EmptyView()
        }
    }
}

/// Shown on days without a planned lifting session while a workout is running. (Starting one is done in the Workout tab.)
struct ResumeWorkoutCard: View {
    @Environment(AppEnvironment.self) private var env

    var body: some View {
        Button { env.selectedTab = .workout } label: {
            Card {
                HStack(spacing: 12) {
                    Image(systemName: "play.fill")
                        .font(.title3).foregroundStyle(Domain.lifting.color).accessibilityHidden(true)
                    VStack(alignment: .leading, spacing: 2) {
                        Text("Resume workout").font(.headline)
                        Text("Your workout is still running.").font(.subheadline).foregroundStyle(.secondary)
                    }
                    Spacer(minLength: 0)
                    Image(systemName: "chevron.right").font(.footnote.weight(.semibold)).foregroundStyle(.tertiary)
                        .accessibilityHidden(true)
                }
            }
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
    }
}

struct AdjustmentNote: View {
    let lines: [String]
    let level: ReadinessLevel

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            ForEach(lines, id: \.self) { line in
                Label(line, systemImage: "exclamationmark.triangle.fill")
                    .font(.footnote)
                    .foregroundStyle(.primary)
                    .labelStyle(.titleAndIcon)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(10)
        .background((level == .unknown ? Color.orange : level.color).opacity(0.15), in: RoundedRectangle(cornerRadius: 12))
    }
}

struct LiftingBody: View {
    @Environment(AppEnvironment.self) private var env
    let session: PrescriptionDTO?
    @State private var setupTarget: MachineSetupTarget?

    var body: some View {
        let exercises = session?.exercises ?? []
        VStack(spacing: 0) {
            ForEach(Array(exercises.enumerated()), id: \.offset) { index, ex in
                if index > 0 { Divider() }
                HStack(alignment: .firstTextBaseline, spacing: 12) {
                    VStack(alignment: .leading, spacing: 2) {
                        Text(ex.name.withoutGymSuffix).font(.subheadline.weight(.medium))
                        if ex.showsSetupButton {
                            Button { setupTarget = MachineSetupTarget(exercise: ex) } label: {
                                Label("Set up machine", systemImage: "wrench.and.screwdriver")
                                    .font(.caption.weight(.semibold))
                            }
                            .buttonStyle(.bordered).tint(.orange).controlSize(.small)
                            .disabled(!env.gyms.canWrite)
                            .accessibilityHint(env.gyms.writeBlockedHint ?? "Opens the machine editor for the current gym")
                        } else if ex.variantId == nil {
                            Text(ex.reason ?? "Log it once to get targets").font(.caption).foregroundStyle(.secondary)
                        }
                        if let note = ex.setupNote {
                            Text(note).font(.caption).foregroundStyle(.secondary)
                        }
                    }
                    Spacer(minLength: 8)
                    Text(PlanFormat.setsSummary(ex.sets))
                        .font(.subheadline.monospacedDigit()).foregroundStyle(.secondary)
                        .multilineTextAlignment(.trailing)
                }
                .padding(.vertical, 8)
            }
        }
        .sheet(item: $setupTarget) { MachineSetupSheet(target: $0) }
    }
}

struct RestBody: View {
    let notes: [String]
    let optional: Bool

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(optional ? "Optional rest day. Easy movement is fine." : "Rest day. Recover, eat, sleep.")
                .font(.subheadline).foregroundStyle(.secondary)
            ForEach(notes, id: \.self) { Text($0).font(.footnote).foregroundStyle(.secondary) }
        }
    }
}

// MARK: - Week strip

struct WeekStrip: View {
    let planned: [CachedPlannedSession]
    let sessions: [LocalSession]
    let runDays: Set<String>
    let today: String

    var body: some View {
        let days = AppClock.weekDays(containing: AppClock.now)
        let cells = WeekStripModel.cells(
            days: days,
            planned: planned.map { PlannedDayRef(id: $0.id, date: $0.date, kind: $0.kind, status: $0.status) },
            finished: sessions.filter { $0.endedAt != nil && $0.deletedAt == nil }
                .map { FinishedDayRef(plannedId: $0.plannedSessionId, day: AppClock.dayString($0.startedAt), kind: $0.kind) },
            runDays: runDays, today: today)
        Card {
            SectionLabel(title: "This week", symbol: "calendar")
            HStack(spacing: 4) {
                ForEach(cells) { DayCell(cell: $0, isToday: $0.day == today) }
            }
            Text("Faint days are your usual pattern where nothing is planned yet.")
                .font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
        }
    }
}

private struct DayCell: View {
    let cell: WeekCellModel
    let isToday: Bool

    private var weekday: String {
        guard let d = AppClock.date(fromDay: cell.day) else { return "" }
        return d.formatted(.dateTime.weekday(.narrow))
    }
    private var color: Color { cell.state == .rest ? .gray : Domain.forKind(cell.kind).color }
    private var faint: Bool { cell.state == .expected }
    private var stateWord: String {
        switch cell.state {
        case .done: "done"
        case .planned: "planned"
        case .missed: "missed"
        case .expected: "usual pattern, not planned yet"
        case .rest: "rest"
        }
    }

    var body: some View {
        VStack(spacing: 6) {
            Text(weekday).font(.caption2.weight(.semibold)).foregroundStyle(isToday ? Color.white : .secondary)
            ZStack(alignment: .bottomTrailing) {
                Image(systemName: PlanFormat.symbol(forKind: cell.kind))
                    .font(.body)
                    .foregroundStyle(isToday ? Color.white : (cell.state == .rest ? Color.secondary.opacity(0.5) : color))
                    .frame(width: 32, height: 32)
                    .background(isToday ? Color.white.opacity(0.25) : color.opacity(cell.state == .rest ? 0.14 : (faint ? 0.05 : 0.14)), in: Circle())
                    .overlay {
                        if faint || cell.state == .missed {
                            Circle().strokeBorder(isToday ? Color.white.opacity(0.7) : (cell.state == .missed ? Color.orange.opacity(0.7) : color.opacity(0.45)),
                                                  style: StrokeStyle(lineWidth: 1, dash: cell.state == .missed ? [] : [3, 3]))
                        }
                    }
                if cell.state == .done {
                    Image(systemName: "checkmark.circle.fill")
                        .font(.caption)
                        .foregroundStyle(.white, .green)
                        .offset(x: 4, y: 4)
                } else if cell.state == .missed {
                    Image(systemName: "minus.circle.fill")
                        .font(.caption)
                        .foregroundStyle(.white, Color.orange.opacity(0.8))
                        .offset(x: 4, y: 4)
                }
            }
            Text(PlanFormat.shortName(forKind: cell.kind))
                .font(.caption2.weight(.medium))
                .foregroundStyle(isToday ? Color.white : .secondary)
                .lineLimit(1).minimumScaleFactor(0.7)
        }
        .opacity(faint && !isToday ? 0.7 : 1)
        .padding(.vertical, 8)
        .frame(maxWidth: .infinity)
        .background(isToday ? Color.accentColor : .clear, in: RoundedRectangle(cornerRadius: 14, style: .continuous))
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("\(cell.day), \(PlanFormat.shortName(forKind: cell.kind)), \(stateWord)")
    }
}

#if DEBUG
#Preview("Today") {
    let env = PreviewData.environment()
    return TodayView().environment(env).modelContainer(env.container)
}
#endif
