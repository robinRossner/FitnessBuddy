import FitnessCore
import SwiftData
import SwiftUI

/// A finished workout: time, volume, personal records and every logged set (read only).
struct WorkoutDetailView: View {
    @Environment(\.modelContext) private var context
    @Query private var sets: [LocalSet]
    let session: LocalSession
    @State private var priorBest: [Int: Double] = [:]

    init(session: LocalSession) {
        self.session = session
        let id = session.id
        _sets = Query(filter: #Predicate<LocalSet> { $0.sessionId == id && $0.deletedAt == nil },
                      sort: [SortDescriptor(\.exerciseOrder), SortDescriptor(\.setOrder)])
    }

    private var logged: [LoggedSet] {
        sets.filter { WorkoutDetailLogic.isLogged(completedAt: $0.completedAt, deletedAt: $0.deletedAt, reps: $0.reps,
                                                  durationS: $0.durationS, startedHere: session.startedHere) }
            .map { LoggedSet(variantId: $0.variantId, variantName: $0.variantName, exerciseOrder: $0.exerciseOrder,
                             setOrder: $0.setOrder, setType: $0.setType, weightKg: $0.weightKg, reps: $0.reps,
                             durationS: $0.durationS) }
    }

    private var lift: Color { Domain.lifting.color }

    var body: some View {
        let blocks = WorkoutDetailLogic.blocks(sets: logged, priorBest: priorBest)
        let prCount = blocks.filter(\.hasPR).count
        ScrollView {
            VStack(alignment: .leading, spacing: Kit.groupSpacing) {
                VStack(alignment: .leading, spacing: 12) {
                    LazyVGrid(columns: [GridItem(.flexible(), spacing: 12), GridItem(.flexible(), spacing: 12)], spacing: 12) {
                        if let end = session.endedAt, end.timeIntervalSince(session.startedAt) >= 60 {
                            MetricTile(label: "Time", symbol: "clock", color: lift,
                                       value: Fmt.longDuration(end.timeIntervalSince(session.startedAt)))
                        }
                        MetricTile(label: "Volume", symbol: "scalemass", color: lift,
                                   value: Fmt.number(WorkoutDetailLogic.volume(logged).rounded(), maxFraction: 0), unit: "kg")
                        MetricTile(label: "Exercises", symbol: "list.bullet", color: lift, value: "\(blocks.count)")
                        MetricTile(label: "Sets", symbol: "checkmark.circle", color: lift, value: "\(logged.count)")
                    }
                    if prCount > 0 {
                        Card {
                            HStack(spacing: 10) {
                                Image(systemName: "star.fill").foregroundStyle(lift).font(.title3).accessibilityHidden(true)
                                Text(prCount == 1 ? "1 personal record" : "\(prCount) personal records").font(.headline)
                            }
                        }
                    }
                    if let notes = session.notes, !notes.isEmpty {
                        Card {
                            CardHeader(title: "Notes", symbol: "note.text", color: lift)
                            Text(notes).font(.subheadline)
                        }
                    }
                }
                PageSection("Exercises", info: "A star marks the set with your best estimated 1RM of the exercise when it beats every earlier logged set of it. The estimate is weight × (1 + reps ÷ 30).") {
                    if blocks.isEmpty {
                        Card { Text("No sets were logged in this workout.").font(.subheadline).foregroundStyle(.secondary) }
                    }
                    ForEach(blocks) { exerciseCard($0) }
                }
            }
            .pagePadding()
        }
        .screenStyle()
        .navigationTitle(session.name.isEmpty ? "Workout" : session.name)
        .navigationBarTitleDisplayMode(.inline)
        .task(id: sets.count) { priorBest = Self.priorBestE1RM(before: session, in: context) }
    }

    private func exerciseCard(_ block: WorkoutDetailLogic.Block) -> some View {
        Card {
            HStack(alignment: .firstTextBaseline, spacing: 6) {
                Text(block.name).font(.headline)
                if block.hasPR { Chip(text: "PR", color: lift, symbol: "star.fill") }
                Spacer(minLength: 8)
                if block.volumeKg > 0 {
                    Text(Fmt.number(block.volumeKg.rounded(), maxFraction: 0) + " kg")
                        .font(.subheadline).foregroundStyle(.secondary)
                }
            }
            Grid(alignment: .leading, horizontalSpacing: 16, verticalSpacing: 6) {
                GridRow {
                    Text("Set"); Text("Weight"); Text("Reps"); Text("e1RM").gridColumnAlignment(.trailing)
                }
                .font(.caption.weight(.semibold))
                .foregroundStyle(.secondary)
                ForEach(block.sets) { set in
                    GridRow {
                        Text(set.label).foregroundStyle(.secondary)
                        Text(set.weightKg.map { "\(Fmt.number($0)) kg" } ?? "–")
                        Text(repsText(set))
                        HStack(spacing: 4) {
                            if set.isPR { Image(systemName: "star.fill").font(.caption).foregroundStyle(lift) }
                            Text(set.e1rm.map { Fmt.number($0, maxFraction: 1) } ?? "–")
                        }
                        .gridColumnAlignment(.trailing)
                    }
                    .font(.subheadline.monospacedDigit())
                    .foregroundStyle(set.isPR ? lift : (set.isWarmup ? Color.secondary : Color.primary))
                }
            }
        }
        .accessibilityElement(children: .contain)
    }

    private func repsText(_ set: WorkoutDetailLogic.SetLine) -> String {
        if let reps = set.reps { return "\(reps)" }
        if let d = set.durationS { return Fmt.duration(d) }
        return "–"
    }

    /// Best e1RM per variant over everything logged before this workout started (own and pulled history).
    static func priorBestE1RM(before session: LocalSession, in context: ModelContext) -> [Int: Double] {
        let own = session.id
        let sets = (try? context.fetch(FetchDescriptor<LocalSet>(
            predicate: #Predicate { $0.deletedAt == nil && $0.reps != nil && $0.sessionId != own }))) ?? []
        guard !sets.isEmpty else { return [:] }
        let started = session.startedAt
        let sessions = (try? context.fetch(FetchDescriptor<LocalSession>(
            predicate: #Predicate { $0.deletedAt == nil && $0.startedAt < started }))) ?? []
        let before = Dictionary(sessions.map { ($0.id, $0) }, uniquingKeysWith: { a, _ in a })
        var best: [Int: Double] = [:]
        for s in sets where s.setType != "warmup" {
            guard let parent = before[s.sessionId],
                  WorkoutDetailLogic.isLogged(completedAt: s.completedAt, deletedAt: s.deletedAt, reps: s.reps,
                                              durationS: s.durationS, startedHere: parent.startedHere),
                  let v = WorkoutDetailLogic.e1rm(weightKg: s.weightKg, reps: s.reps) else { continue }
            best[s.variantId] = max(best[s.variantId] ?? 0, v)
        }
        return best
    }
}
