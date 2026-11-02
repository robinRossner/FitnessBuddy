import FitnessCore
import SwiftData
import SwiftUI

extension WorkoutChooserLogic {
    // MARK: Database access

    @MainActor
    static func choices(in ctx: ModelContext, today: String = AppClock.todayString) -> [WorkoutChoice] {
        let rows = (try? ctx.fetch(FetchDescriptor<CachedPlannedSession>(sortBy: [SortDescriptor(\.date)]))) ?? []
        let finishedSessions = (try? ctx.fetch(FetchDescriptor<LocalSession>(
            predicate: #Predicate { $0.endedAt != nil && $0.deletedAt == nil },
            sortBy: [SortDescriptor(\.startedAt, order: .reverse)]))) ?? []
        let doneIds = Set(finishedSessions.compactMap(\.plannedSessionId))
        let planned = rows.compactMap { row -> PlannedLiftRef? in
            guard liftingKinds.contains(row.kind) else { return nil }
            return PlannedLiftRef(id: row.id, date: row.date, kind: row.kind, title: row.title,
                                  done: row.status == "done" || doneIds.contains(row.id),
                                  exerciseCount: row.item?.session?.exercises?.count ?? 0)
        }
        let finished = finishedSessions.map { FinishedLiftRef(id: $0.id, kind: $0.kind, name: $0.name, startedAt: $0.startedAt) }
        return choices(planned: planned, finished: finished, today: today)
    }
}

// MARK: - Chooser sheet

struct WorkoutChooserSheet: View {
    @Environment(AppEnvironment.self) private var env
    @Environment(\.dismiss) private var dismiss
    // Observed so the list follows the cache while the sheet is open.
    @Query private var planned: [CachedPlannedSession]
    @Query(filter: #Predicate<LocalSession> { $0.endedAt != nil && $0.deletedAt == nil }) private var finished: [LocalSession]
    @State private var selection: String?

    var body: some View {
        let choices = WorkoutChooserLogic.choices(in: env.context)
        let selected = choices.first { $0.id == selection } ?? choices.first
        NavigationStack {
            VStack(spacing: 0) {
                List {
                    let sessions = choices.filter { $0.source != .empty }
                    if !sessions.isEmpty {
                        Section(header: Text(isTemplate(sessions) ? "Repeat a recent session" : "Next planned sessions"),
                                footer: isTemplate(sessions) ? Text("Nothing is planned in the cached week. Same exercises, last weights and reps.") : nil) {
                            ForEach(sessions) { row(for: $0, selected: selected) }
                        }
                    }
                    Section {
                        ForEach(choices.filter { $0.source == .empty }) { row(for: $0, selected: selected) }
                    }
                }
                .listStyle(.insetGrouped)

                Button {
                    if let selected { env.workoutFlow.start(selected) }
                    dismiss()
                } label: {
                    Label("Start", systemImage: "play.fill").font(.headline).frame(maxWidth: .infinity, minHeight: 36)
                }
                .buttonStyle(.borderedProminent).tint(Domain.lifting.color)
                .padding(.horizontal, 20).padding(.vertical, 12)
            }
            .background(Color.screenBackground)
            .navigationTitle("Start workout")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } } }
        }
        .presentationDetents([.fraction(0.8), .large])
        .onChange(of: planned.count) { _, _ in selection = nil }
        .onChange(of: finished.count) { _, _ in selection = nil }
    }

    private func isTemplate(_ sessions: [WorkoutChoice]) -> Bool {
        if case .template = sessions.first?.source { return true }
        return false
    }

    private func row(for choice: WorkoutChoice, selected: WorkoutChoice?) -> some View {
        let isSelected = choice.id == selected?.id
        return Button { selection = choice.id } label: {
            HStack(spacing: 12) {
                Image(systemName: isSelected ? "checkmark.circle.fill" : "circle")
                    .font(.title3)
                    .foregroundStyle(isSelected ? Domain.lifting.color : Color.secondary)
                    .accessibilityHidden(true)
                VStack(alignment: .leading, spacing: 2) {
                    Text(choice.title).font(.body.weight(.medium)).foregroundStyle(.primary)
                    if !choice.detail.isEmpty || choice.exerciseCount != nil {
                        Text(choice.subtitle).font(.subheadline).foregroundStyle(.secondary)
                    }
                }
                .fixedSize(horizontal: false, vertical: true)
                Spacer(minLength: 0)
            }
            .contentShape(Rectangle())
            .padding(.vertical, 4)
        }
        .buttonStyle(.plain)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(choice.label)
        .accessibilityAddTraits(isSelected ? [.isButton, .isSelected] : .isButton)
    }
}
