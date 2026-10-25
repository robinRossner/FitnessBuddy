import SwiftData
import SwiftUI

struct WorkoutTabView: View {
    @Environment(AppEnvironment.self) private var env
    @Query(filter: #Predicate<LocalSession> { $0.endedAt == nil && $0.deletedAt == nil && $0.startedHere },
           sort: \LocalSession.startedAt, order: .reverse)
    private var active: [LocalSession]

    var body: some View {
        @Bindable var env = env
        NavigationStack {
            if let session = active.first {
                ActiveWorkoutView(session: session)
            } else {
                WorkoutStartView()
            }
        }
        .sheet(item: $env.finishedSummary) { summary in
            WorkoutSummarySheet(summary: summary)
        }
    }
}

// MARK: - Idle state

struct WorkoutStartView: View {
    @Environment(AppEnvironment.self) private var env
    @Query(sort: \CachedPlannedSession.date) private var planned: [CachedPlannedSession]
    @Query(filter: #Predicate<LocalSession> { $0.endedAt != nil && $0.deletedAt == nil },
           sort: \LocalSession.startedAt, order: .reverse)
    private var finished: [LocalSession]

    private var todaysLifting: [CachedPlannedSession] {
        planned.filter { $0.date == AppClock.todayString && ["upper", "lower", "full"].contains($0.kind) }
    }

    var body: some View {
        List {
            if !todaysLifting.isEmpty {
                Section("Planned today") {
                    ForEach(todaysLifting, id: \.id) { row in
                        Button { env.startWorkout(from: row) } label: {
                            HStack {
                                VStack(alignment: .leading, spacing: 2) {
                                    Text(row.title).font(.headline)
                                    Text("\(row.item?.session?.exercises?.count ?? 0) exercises")
                                        .font(.subheadline).foregroundStyle(.secondary)
                                }
                                Spacer()
                                Image(systemName: "play.circle.fill").font(.title).foregroundStyle(Domain.lifting.color)
                            }
                            .contentShape(Rectangle())
                        }
                        .buttonStyle(.plain)
                    }
                }
            }
            Section {
                Button { env.startWorkout(from: nil) } label: {
                    Label("Start empty workout", systemImage: "plus.circle.fill")
                        .font(.headline).frame(minHeight: 36)
                }
            }
            if !finished.isEmpty {
                Section("Recent") {
                    ForEach(finished.prefix(10)) { s in
                        VStack(alignment: .leading, spacing: 2) {
                            Text(s.name.isEmpty ? "Workout" : s.name).font(.headline)
                            Text("\(s.startedAt.formatted(.dateTime.weekday(.wide).day().month())) · \(Fmt.longDuration((s.endedAt ?? s.startedAt).timeIntervalSince(s.startedAt)))")
                                .font(.subheadline).foregroundStyle(.secondary)
                        }
                    }
                }
            }
        }
        .navigationTitle("Workout")
        .overlay {
            if todaysLifting.isEmpty && finished.isEmpty { Color.clear }
        }
    }
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

                Grid(horizontalSpacing: 24, verticalSpacing: 20) {
                    GridRow {
                        StatTile(title: "Duration", value: Fmt.duration(summary.duration), color: Domain.lifting.color)
                        StatTile(title: "Sets", value: "\(summary.setCount)")
                    }
                    GridRow {
                        StatTile(title: "Exercises", value: "\(summary.exerciseCount)")
                        StatTile(title: "Volume", value: Fmt.number(summary.volumeKg.rounded(), maxFraction: 0), unit: "kg")
                    }
                }
                .padding(20)
                .background(Color.cardBackground, in: RoundedRectangle(cornerRadius: 20, style: .continuous))

                Label("Saved on this device and queued for sync.", systemImage: "icloud.and.arrow.up")
                    .font(.footnote).foregroundStyle(.secondary)
                Spacer()
                Button { dismiss() } label: {
                    Text("Done").font(.headline).frame(maxWidth: .infinity, minHeight: 36)
                }
                .buttonStyle(.borderedProminent).tint(Domain.lifting.color)
            }
            .padding(.horizontal, 20)
            .padding(.bottom, 12)
            .background(Color.screenBackground)
            .navigationTitle("Workout complete")
            .navigationBarTitleDisplayMode(.inline)
        }
        .presentationDetents([.large])
    }
}

#if DEBUG
#Preview("Workout") {
    let env = PreviewData.environment(startWorkout: true)
    return WorkoutTabView().environment(env).modelContainer(env.container)
}
#endif
