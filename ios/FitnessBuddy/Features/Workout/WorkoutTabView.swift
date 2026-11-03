import FitnessCore
import SwiftData
import SwiftUI

/// Screens the Workout stack can be told to show (only the screenshot launch hooks fill `path`).
enum WorkoutRoute: Hashable {
    case plan(String)
    case detail(UUID)
}

struct WorkoutTabView: View {
    @Environment(AppEnvironment.self) private var env
    @State private var path: [WorkoutRoute] = []
    @Query(filter: #Predicate<LocalSession> { $0.endedAt == nil && $0.deletedAt == nil && $0.startedHere },
           sort: \LocalSession.startedAt, order: .reverse)
    private var active: [LocalSession]

    var body: some View {
        @Bindable var flow = env.workoutFlow
        NavigationStack(path: $path) {
            Group {
                if let session = active.first {
                    ActiveWorkoutView(session: session)
                } else {
                    WorkoutStartView()
                }
            }
            .navigationDestination(for: WorkoutRoute.self) { route in
                switch route {
                case .plan(let id):
                    if let row = try? env.context.fetch(FetchDescriptor<CachedPlannedSession>(predicate: #Predicate { $0.id == id })).first {
                        PlannedLiftView(row: row).screenIdentifier(AXID.Workout.plan)
                    }
                case .detail(let id):
                    if let session = try? env.context.fetch(FetchDescriptor<LocalSession>(predicate: #Predicate { $0.id == id })).first {
                        WorkoutDetailView(session: session)
                    }
                }
            }
        }
        .screenIdentifier(AXID.Screen.workout)
        .sheet(item: $flow.finishedSummary) { summary in
            WorkoutSummarySheet(summary: summary)
        }
        #if DEBUG
        .task {
            let routes = await WorkoutUIHooks.prepare(env)
            if !routes.isEmpty { path = routes }
        }
        #endif
    }
}

// MARK: - Idle state

/// The Workout tab while nothing is running: next workout, this week, recent workouts and the library.
/// A running workout replaces this screen (`WorkoutTabView` shows `ActiveWorkoutView` instead), so there is no separate
/// resume card: opening the tab lands straight in the workout.
struct WorkoutStartView: View {
    @Environment(AppEnvironment.self) private var env
    @Query(sort: \CachedPlannedSession.date) private var planned: [CachedPlannedSession]
    @Query(filter: #Predicate<LocalSession> { $0.endedAt != nil && $0.deletedAt == nil },
           sort: \LocalSession.startedAt, order: .reverse)
    private var finished: [LocalSession]
    @Query(filter: #Predicate<CachedGym> { $0.deletedAt == nil }, sort: \CachedGym.name) private var cachedGyms: [CachedGym]

    private var lift: Color { Domain.lifting.color }
    private var today: String { AppClock.todayString }

    private var refs: (planned: [PlannedLiftRef], finished: [FinishedLiftRef]) {
        WorkoutHomeLogic.refs(planned: planned, finished: finished)
    }

    private var next: WorkoutChoice? {
        WorkoutChooserLogic.choices(planned: refs.planned, finished: refs.finished, today: today).first { $0.source != .empty }
    }

    private var weekRows: [WorkoutWeekRow] {
        let days = AppClock.weekDays(containing: AppClock.now)
        let sessions = finished.map {
            FinishedWeekRef(id: $0.id, plannedId: $0.plannedSessionId, day: AppClock.dayString($0.startedAt), name: $0.name)
        }
        return WorkoutHomeLogic.week(days: days, planned: refs.planned, finished: sessions, today: today)
    }

    private var currentGymName: String? {
        guard let id = env.gyms.currentGymId else { return nil }
        return env.gyms.list?.gyms.first { $0.id == id }?.name ?? cachedGyms.first { $0.id == id }?.name
    }

    var body: some View {
        let rows = weekRows
        ScrollView {
            VStack(alignment: .leading, spacing: Kit.groupSpacing) {
                nextCard
                PageSection("This Week", info: "Lifting sessions of this week: finished ones open the workout, planned ones open the plan. Running is tracked on the watch and shows up under Progress.") {
                    thisWeek(rows)
                } trailing: {
                    if !rows.isEmpty {
                        Text("\(rows.filter { $0.state == .done }.count) of \(rows.count) done")
                            .font(.subheadline).foregroundStyle(.secondary)
                    }
                }
                if !finished.isEmpty {
                    PageSection("Recent") { recent }
                }
                PageSection("Library") { library }
            }
            .pagePadding()
        }
        .screenStyle()
        .navigationTitle("Workout")
        .task {
            await env.gyms.loadList()
            if let id = env.gyms.currentGymId { await env.gyms.loadMachines(gymId: id) }
        }
    }

    // MARK: Next workout

    @ViewBuilder private var nextCard: some View {
        if let next, case .planned(let id) = next.source, let row = planned.first(where: { $0.id == id }) {
            NavigationLink {
                PlannedLiftView(row: row).screenIdentifier(AXID.Workout.plan)
            } label: {
                nextLabel(title: next.title, when: next.detail.isEmpty ? nil : next.detail.capitalizedFirst,
                          line: planLine(row), action: "View Plan & Start")
            }
            .buttonStyle(.plain)
            .accessibilityIdentifier(AXID.Workout.nextCard)
        } else if let next {
            Button { env.workoutFlow.start(next) } label: {
                nextLabel(title: next.title, when: next.detail.isEmpty ? nil : next.detail.capitalizedFirst,
                          line: "Same exercises, last weights and reps", action: "Start Workout")
            }
            .buttonStyle(.plain)
        } else {
            Button { env.workoutFlow.start(from: nil) } label: {
                nextLabel(title: "Empty Workout", when: nil, line: "Add exercises as you go", action: "Start Workout")
            }
            .buttonStyle(.plain)
        }
    }

    private func planLine(_ row: CachedPlannedSession) -> String {
        let exercises = row.item?.session?.exercises ?? []
        guard !exercises.isEmpty else { return "Planned session" }
        let sets = exercises.reduce(0) { $0 + $1.sets.count }
        return "\(exercises.count) exercises · \(sets) sets"
    }

    private func nextLabel(title: String, when: String?, line: String, action: String) -> some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack {
                Label("Next Workout", systemImage: "dumbbell.fill").font(.subheadline.weight(.semibold))
                Spacer()
                if let when { Text(when).font(.subheadline.weight(.medium)) }
            }
            .foregroundStyle(.white.opacity(0.9))

            VStack(alignment: .leading, spacing: 2) {
                Text(title).font(.system(.largeTitle, design: .rounded).weight(.bold)).minimumScaleFactor(0.7).lineLimit(2)
                Text(line).font(.subheadline).foregroundStyle(.white.opacity(0.85))
            }
            .foregroundStyle(.white)

            HStack(spacing: 6) {
                Image(systemName: "play.fill").accessibilityHidden(true)
                Text(action)
            }
            .font(.headline)
            .foregroundStyle(lift)
            .padding(.horizontal, 16)
            .padding(.vertical, 10)
            .background(.white, in: Capsule())
        }
        .padding(18)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(LinearGradient(colors: [lift, lift.mix(with: .red, by: 0.35)], startPoint: .topLeading, endPoint: .bottomTrailing),
                    in: RoundedRectangle(cornerRadius: Kit.cardRadius + 4, style: .continuous))
        .contentShape(Rectangle())
        .accessibilityElement(children: .combine)
    }

    // MARK: This week

    @ViewBuilder private func thisWeek(_ rows: [WorkoutWeekRow]) -> some View {
        Card {
            if rows.isEmpty {
                Text("Nothing planned this week.").font(.subheadline).foregroundStyle(.secondary)
            } else {
                HStack(spacing: 6) {
                    ForEach(rows) { row in
                        Capsule()
                            .fill(row.state == .done ? AnyShapeStyle(lift.gradient) : AnyShapeStyle(Color(.systemGray5)))
                            .frame(height: 8)
                    }
                }
                .accessibilityHidden(true)
                VStack(spacing: 0) {
                    ForEach(Array(rows.enumerated()), id: \.element.id) { n, row in
                        if n > 0 { Divider() }
                        weekRow(row)
                    }
                }
            }
        }
    }

    @ViewBuilder private func weekRow(_ row: WorkoutWeekRow) -> some View {
        let done = row.state == .done
        let session = row.sessionId.flatMap { id in finished.first { $0.id == id } }
        let plan = row.plannedId.flatMap { id in planned.first { $0.id == id } }
        let label = HStack(spacing: 12) {
            Image(systemName: done ? "checkmark.circle.fill" : (row.state == .missed ? "exclamationmark.circle" : "circle.dashed"))
                .font(.title3)
                .foregroundStyle(done ? lift : (row.state == .missed ? Color.orange : Color.secondary))
                .accessibilityHidden(true)
            VStack(alignment: .leading, spacing: 1) {
                Text(row.title).font(.body.weight(.medium))
                Text("\(dayName(row.day)) · \(done ? "Done" : (row.state == .missed ? "Missed" : "Planned"))")
                    .font(.subheadline).foregroundStyle(.secondary)
            }
        }
        if done, let session {
            ChevronRowLink { WorkoutDetailView(session: session) } label: { label }
        } else if !done, let plan {
            ChevronRowLink { PlannedLiftView(row: plan).screenIdentifier(AXID.Workout.plan) } label: { label }
        } else {
            label.padding(.vertical, 10)
        }
    }

    private func dayName(_ day: String) -> String {
        if day == today { return "Today" }
        return AppClock.date(fromDay: day)?.formatted(.dateTime.weekday(.wide)) ?? day
    }

    // MARK: Recent

    private var recent: some View {
        Card(padding: 0) {
            VStack(spacing: 0) {
                ForEach(Array(finished.prefix(10).enumerated()), id: \.element.id) { n, session in
                    if n > 0 { Divider().padding(.leading, 16) }
                    ChevronRowLink { WorkoutDetailView(session: session) } label: { RecentWorkoutLabel(session: session) }
                        .padding(.horizontal, 16)
                }
            }
        }
    }

    // MARK: Library

    private var library: some View {
        Card(padding: 0) {
            VStack(spacing: 0) {
                Button { env.workoutFlow.showChooser = true } label: {
                    libraryLabel("Choose a Workout", symbol: "list.bullet.rectangle", detail: "Plan, repeat or empty")
                        .padding(.vertical, 10)
                        .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .accessibilityIdentifier(AXID.Workout.chooseButton)
                .padding(.horizontal, 16)
                Divider().padding(.leading, 16)
                ChevronRowLink { GymManageView() } label: {
                    libraryLabel("Gyms", symbol: "building.2", detail: currentGymName ?? "")
                }
                .padding(.horizontal, 16)
                if let id = env.gyms.currentGymId, let name = currentGymName {
                    Divider().padding(.leading, 16)
                    ChevronRowLink { GymMachinesView(gymId: id, gymName: name) } label: {
                        libraryLabel("Machines", symbol: "gearshape.2", detail: MachineLogic.needsSetupText(env.gyms.unmappedCount) ?? name,
                                     detailColor: env.gyms.unmappedCount ?? 0 > 0 ? .orange : .secondary)
                    }
                    .padding(.horizontal, 16)
                }
            }
        }
    }

    private func libraryLabel(_ title: String, symbol: String, detail: String, detailColor: Color = .secondary) -> some View {
        HStack(spacing: 12) {
            Image(systemName: symbol).foregroundStyle(lift).frame(width: 24).accessibilityHidden(true)
            Text(title).font(.body.weight(.medium))
            Spacer(minLength: 8)
            Text(detail).font(.subheadline).foregroundStyle(detailColor).lineLimit(1)
        }
    }
}

/// "Upper · Mon 26 Oct · 52 min · 8,400 kg" for one finished workout.
private struct RecentWorkoutLabel: View {
    let session: LocalSession
    @Query private var sets: [LocalSet]

    init(session: LocalSession) {
        self.session = session
        let id = session.id
        _sets = Query(filter: #Predicate<LocalSet> { $0.sessionId == id && $0.deletedAt == nil })
    }

    private var detail: String {
        var parts = [session.startedAt.formatted(.dateTime.weekday(.abbreviated).day().month(.abbreviated))]
        if let end = session.endedAt, end.timeIntervalSince(session.startedAt) >= 60 {
            parts.append(Fmt.longDuration(end.timeIntervalSince(session.startedAt)))
        }
        let logged = sets.filter {
            WorkoutDetailLogic.isLogged(completedAt: $0.completedAt, deletedAt: $0.deletedAt, reps: $0.reps,
                                        durationS: $0.durationS, startedHere: session.startedHere)
        }.map {
            LoggedSet(variantId: $0.variantId, variantName: $0.variantName, exerciseOrder: $0.exerciseOrder, setOrder: $0.setOrder,
                      setType: $0.setType, weightKg: $0.weightKg, reps: $0.reps, durationS: $0.durationS)
        }
        let volume = WorkoutDetailLogic.volume(logged)
        if volume > 0 { parts.append(Fmt.volume(volume)) }
        return parts.joined(separator: " · ")
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(session.name.isEmpty ? "Workout" : session.name).font(.body.weight(.medium))
            Text(detail).font(.subheadline).foregroundStyle(.secondary).lineLimit(1).minimumScaleFactor(0.85)
        }
    }
}

private extension String {
    var capitalizedFirst: String { prefix(1).uppercased() + dropFirst() }
}

// MARK: - Summary

struct WorkoutSummarySheet: View {
    @Environment(\.dismiss) private var dismiss
    let summary: WorkoutSummary

    var body: some View {
        NavigationStack {
            VStack(spacing: 24) {
                Ring(color: Domain.lifting.color, lineWidth: 12) {
                    Image(systemName: "checkmark").font(.largeTitle.weight(.bold)).foregroundStyle(Domain.lifting.color)
                }
                .frame(width: 110, height: 110)
                .padding(.top, 24)
                .accessibilityHidden(true)

                Text(summary.name).font(.title2.weight(.semibold))

                LazyVGrid(columns: [GridItem(.flexible(), spacing: 12), GridItem(.flexible(), spacing: 12)], spacing: 12) {
                    MetricTile(label: "Duration", symbol: "clock", color: Domain.lifting.color, value: Fmt.duration(summary.duration))
                    MetricTile(label: "Sets", symbol: "checkmark.circle", color: Domain.lifting.color, value: "\(summary.setCount)")
                    MetricTile(label: "Exercises", symbol: "list.bullet", color: Domain.lifting.color, value: "\(summary.exerciseCount)")
                    MetricTile(label: "Volume", symbol: "scalemass", color: Domain.lifting.color,
                               value: Fmt.number(summary.volumeKg.rounded(), maxFraction: 0), unit: "kg")
                }

                Label("Saved on this device and queued for sync.", systemImage: "icloud.and.arrow.up")
                    .font(.footnote).foregroundStyle(.secondary)
                Spacer()
                Button { dismiss() } label: {
                    Text("Done").font(.headline).frame(maxWidth: .infinity, minHeight: 36)
                }
                .buttonStyle(.borderedProminent).tint(Domain.lifting.color)
            }
            .padding(.horizontal, 16)
            .padding(.bottom, 12)
            .screenStyle()
            .navigationTitle("Workout complete")
            .navigationBarTitleDisplayMode(.inline)
        }
        .screenIdentifier(AXID.Workout.summary)
        .stableForScreenshots()
        .presentationDetents([.large])
    }
}

#if DEBUG
#Preview("Workout") {
    let env = PreviewData.environment(startWorkout: true)
    return WorkoutTabView().environment(env).modelContainer(env.container)
}
#endif
