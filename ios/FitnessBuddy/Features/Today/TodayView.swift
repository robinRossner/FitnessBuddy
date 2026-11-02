import FitnessCore
import SwiftData
import SwiftUI

/// The Summary tab (home): Today (readiness, today's runs / rest, the next lift, a running workout, the coach), This Week,
/// Pinned metric cards that open their chart, Highlights and the strongest Insight. Everything comes from the cached plan,
/// the local workouts and the stats endpoints; a part without data is left out.
struct TodayView: View {
    @Environment(AppEnvironment.self) private var env
    @Query(sort: \CachedPlannedSession.date) private var planned: [CachedPlannedSession]
    @Query private var states: [SyncState]
    @Query(filter: #Predicate<LocalSession> { $0.deletedAt == nil }, sort: \LocalSession.startedAt, order: .reverse)
    private var sessions: [LocalSession]
    @Query(sort: \LocalBodyMeasurement.measuredAt, order: .reverse) private var measurements: [LocalBodyMeasurement]
    @AppStorage(SummaryLogic.pinnedKey) private var pinnedRaw = SummaryLogic.defaultPinnedRaw
    @State private var showSettings: Bool
    @State private var editingPins = false
    private let openSheet: String?

    /// `-uiSheet settings|readiness|run|weekrun|pins` opens that sheet at launch (screenshots, DEBUG only).
    init() {
        #if DEBUG
        openSheet = UserDefaults.standard.string(forKey: "uiSheet")
        #else
        openSheet = nil
        #endif
        _showSettings = State(initialValue: openSheet == "settings")
        _editingPins = State(initialValue: openSheet == "pins")
    }

    // MARK: Data

    private var health: StatsResource<HealthStatsDTO> { env.stats.resource(StatsEndpoints.health()) }
    private var volume: StatsResource<WeeklyVolumeDTO> { env.stats.resource(StatsEndpoints.weeklyVolume()) }
    private var lifts: StatsResource<LiftsStatsDTO> { env.stats.resource(StatsEndpoints.lifts) }
    private var correlations: StatsResource<CorrelationsDTO> { env.stats.resource(StatsEndpoints.correlations()) }
    private var recentRuns: StatsResource<[RunSummaryDTO]> { env.stats.resource(StatsEndpoints.recentRuns()) }
    private var resources: [any StatsLoadable] { [health, volume, lifts, correlations, recentRuns] }

    private var today: String { AppClock.todayString }
    private var todayItems: [CachedPlannedSession] { planned.filter { $0.date == today } }
    private var coachPending: Int { states.first?.coachPendingCount ?? 0 }
    private var activeSession: LocalSession? { sessions.first { $0.isActive } }
    private var finished: [LocalSession] { sessions.filter { $0.endedAt != nil } }

    private var readinessSummary: ReadinessSummary? {
        let state = states.first.flatMap { $0.readinessDate == today ? $0 : nil }
        let legacy = state?.readiness ?? todayItems.compactMap { $0.item?.readiness }.first { $0.color != nil }
        return ReadinessSummary.make(today: state?.readinessToday, legacy: legacy, day: nil)
    }

    private var runDays: Set<String> { Set((recentRuns.value ?? []).compactMap(\.day)) }

    private var weekRuns: WeekRunsModel {
        WeekRunsModel.build(
            days: AppClock.weekDays(containing: AppClock.now),
            items: planned.compactMap(\.item),
            runs: recentRuns.value ?? [],
            finished: finished.map { FinishedDayRef(plannedId: $0.plannedSessionId, day: AppClock.dayString($0.startedAt), kind: $0.kind) },
            today: today)
    }

    private func completedSession(for row: CachedPlannedSession) -> LocalSession? {
        finished.first { $0.plannedSessionId == row.id }
    }

    private var nextLift: (row: CachedPlannedSession, when: String, subtitle: String)? {
        let doneIds = Set(finished.compactMap(\.plannedSessionId))
        let refs = planned.compactMap { row -> PlannedLiftRef? in
            guard WorkoutChooserLogic.liftingKinds.contains(row.kind) else { return nil }
            return PlannedLiftRef(id: row.id, date: row.date, kind: row.kind, title: row.title,
                                  done: row.status == "done" || doneIds.contains(row.id),
                                  exerciseCount: row.item?.session?.exercises?.count ?? 0)
        }
        guard let next = SummaryLogic.nextLift(planned: refs, today: today),
              let row = planned.first(where: { $0.id == next.id }) else { return nil }
        return (row, next.when, next.subtitle)
    }

    private var metricSources: MetricCatalog.Sources {
        MetricCatalog.Sources(health: health.value, volume: volume.value, localWeights: measurements.map(BodyweightLog.snapshot))
    }

    // MARK: Body

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: Kit.groupSpacing) {
                    PageSection("Today") { todayGroup } trailing: {
                        Text(AppClock.now.formatted(.dateTime.weekday(.wide).day().month(.abbreviated)))
                            .font(.subheadline).foregroundStyle(.secondary)
                    }
                    thisWeek
                    pinnedGroup
                    highlightsGroup
                    insightsGroup
                    FreshnessLabel(resource: health)
                }
                .pagePadding()
            }
            .screenStyle()
            .navigationTitle("Summary")
            .toolbar {
                ToolbarItem(placement: .topBarLeading) {
                    HStack(spacing: 12) { SyncStatusBadge(); GarminSyncButton(resources: resources) }
                }
                ToolbarItem(placement: .topBarTrailing) {
                    Button { showSettings = true } label: { Image(systemName: "gearshape") }
                        .accessibilityLabel("Settings")
                }
            }
            .refreshable { await refreshStats(env, resources) }
            .loadsStats(resources)
            .garminSyncBanner()
            .sheet(isPresented: $showSettings) { SettingsView() }
            .sheet(isPresented: $editingPins) { PinEditorSheet(pinnedRaw: $pinnedRaw) }
        }
    }

    // MARK: Today

    @ViewBuilder private var todayGroup: some View {
        let readinessTime = states.first?.readinessDate == today ? "Today" : nil
        ReadinessCard(summary: readinessSummary, time: readinessTime, startOpen: openSheet == "readiness")

        if planned.isEmpty {
            EmptyPlanCard(isConfigured: env.settings.isConfigured) { showSettings = true }
        }
        let firstRunId = todayItems.first { Domain.forKind($0.kind) == .running }?.id
        ForEach(todayItems, id: \.id) { row in
            if let item = row.item {
                switch Domain.forKind(row.kind) {
                case .running: TodayRunCard(item: item, openDetail: openSheet == "run" && row.id == firstRunId)
                case .lifting:
                    if let done = completedSession(for: row) { LiftDoneCard(row: row, completed: done) }
                default: RestDayCard(item: item)
                }
            }
        }
        if let activeSession {
            ResumeWorkoutCard(session: activeSession)
        } else if let next = nextLift {
            NextLiftCard(row: next.row, when: next.when, subtitle: next.subtitle)
        }
        if CoachLogic.bannerVisible(pendingCount: coachPending) {
            CoachSummaryCard(pendingCount: coachPending, latestId: states.first?.coachLatestPendingId)
        }
    }

    @ViewBuilder private var thisWeek: some View {
        if !planned.isEmpty {
            PageSection("This Week") {
                WeekRunsCard(model: weekRuns, today: today, openFirstAdaptive: openSheet == "weekrun")
                WeekStrip(planned: planned, sessions: sessions, runDays: runDays, today: today)
            }
        }
    }

    // MARK: Pinned

    private var pinnedGroup: some View {
        let ids = SummaryLogic.pinnedIds(pinnedRaw)
        let sources = metricSources
        return PageSection("Pinned") {
            if ids.isEmpty {
                Card {
                    Text("Nothing pinned. Tap Edit to choose the metrics you want here.")
                        .font(.subheadline).foregroundStyle(.secondary)
                }
            }
            ForEach(ids, id: \.self) { id in
                if let metric = MetricCatalog.info(id) {
                    MetricCard(metric: metric, points: MetricCatalog.points(metric, from: sources)) {
                        LiveMetricDetailView(metric: metric)
                    }
                }
            }
        } trailing: {
            Button("Edit") { editingPins = true }
        }
    }

    // MARK: Highlights

    @ViewBuilder private var highlightsGroup: some View {
        let items = SummaryLogic.highlights(volume: volume.value?.weeks ?? [], health: health.value?.summary,
                                            lifts: lifts.value?.lifts ?? [])
        if !items.isEmpty {
            PageSection("Highlights") {
                ForEach(items) { h in
                    HighlightCard(title: h.title, symbol: h.symbol, color: h.domain.color, sentence: h.sentence, bars: h.bars) {
                        switch h.target {
                        case .metric(let id):
                            if let metric = MetricCatalog.info(id) { LiveMetricDetailView(metric: metric) }
                        case .lift(let lift):
                            LiftDetailView(lift: lift)
                        }
                    }
                }
            }
        }
    }

    // MARK: Insights

    @ViewBuilder private var insightsGroup: some View {
        if let featured = InsightTransforms.strongest(correlations.value?.items ?? []) {
            PageSection("Insights", info: "How your sleep, recovery and training move together, from the last 120 days.") {
                NavigationLink {
                    InsightDetailView(item: featured)
                } label: {
                    Card {
                        CardHeader(title: "Strongest Link", symbol: Domain.insights.symbol, color: Domain.insights.color,
                                   time: InsightTransforms.category(of: featured).title, chevron: true)
                        InsightRow(item: featured)
                    }
                }
                .buttonStyle(.plain)
            } trailing: {
                NavigationLink("Show All") { InsightsView() }
            }
        }
    }
}

#if DEBUG
#Preview("Summary") {
    let env = PreviewData.environment()
    return TodayView().environment(env).modelContainer(env.container)
}
#endif
