import SwiftData
import SwiftUI

/// One row of the "Start workout" chooser.
struct WorkoutChoice: Identifiable, Equatable {
    enum Source: Equatable {
        /// A cached planned lifting session (starting it links `planned_session_id`).
        case planned(String)
        /// A finished session used as a template (no lifting session is planned at all).
        case template(UUID)
        case empty
    }

    var id: String
    var source: Source
    var title: String
    /// "planned Mon", "last done Thu", ...
    var detail: String
    var exerciseCount: Int?

    /// "Planned Mon · 5 exercises"
    var subtitle: String {
        let first: String? = detail.isEmpty ? nil : String(detail.prefix(1)).uppercased() + String(detail.dropFirst())
        let count: String? = exerciseCount.map { "\($0) exercises" }
        return [first, count].compactMap { $0 }.joined(separator: " · ")
    }

    /// "Upper (planned Mon)"
    var label: String { detail.isEmpty ? title : "\(title) (\(detail))" }
}

/// A cached planned lifting session, reduced to what the chooser needs.
struct PlannedLiftRef: Equatable {
    var id: String
    var date: String
    var kind: String
    var title: String
    var done: Bool
    var exerciseCount: Int
}

/// A finished session that can serve as a template.
struct FinishedLiftRef: Equatable {
    var id: UUID
    var kind: String
    var name: String
    var startedAt: Date
}

enum WorkoutChooserLogic {
    static let liftingKinds: Set<String> = ["upper", "lower", "full"]
    static let maxPlanned = 4

    /// Order: not-yet-done planned lifting sessions from today on (soonest first), then missed ones from earlier in the
    /// cached week (most recent first), then "Empty workout". Only when nothing planned is left, the most recent finished
    /// session of each kind (the one done longest ago first, so the rotation continues) are offered as templates.
    /// The first element is the preselected one.
    static func choices(planned: [PlannedLiftRef], finished: [FinishedLiftRef], today: String,
                        locale: Locale = .current, calendar: Calendar = AppClock.calendar) -> [WorkoutChoice] {
        let open = planned.filter { !$0.done && liftingKinds.contains($0.kind) }
        let upcoming = open.filter { $0.date >= today }.sorted { ($0.date, $0.title) < ($1.date, $1.title) }
        let missed = open.filter { $0.date < today }.sorted { ($0.date, $0.title) > ($1.date, $1.title) }
        var out: [WorkoutChoice] = (upcoming + missed).prefix(maxPlanned).map { p in
            WorkoutChoice(id: "planned-\(p.id)", source: .planned(p.id), title: p.title,
                          detail: "planned \(dayWord(p.date, today: today, locale: locale, calendar: calendar))",
                          exerciseCount: p.exerciseCount)
        }
        if out.isEmpty {
            out = templates(finished, locale: locale)
        }
        out.append(WorkoutChoice(id: "empty", source: .empty, title: "Empty workout", detail: "", exerciseCount: nil))
        return out
    }

    private static func templates(_ finished: [FinishedLiftRef], locale: Locale) -> [WorkoutChoice] {
        let newestFirst = finished.sorted { $0.startedAt > $1.startedAt }
        var byKind: [FinishedLiftRef] = []
        for kind in ["upper", "lower", "full"] {
            if let latest = newestFirst.first(where: { $0.kind == kind }) { byKind.append(latest) }
        }
        if byKind.isEmpty, let any = newestFirst.first { byKind = [any] }
        return byKind.sorted { $0.startedAt < $1.startedAt }.map { f in
            let name = f.name.isEmpty ? f.kind.capitalized : f.name
            return WorkoutChoice(id: "template-\(f.id.uuidString)", source: .template(f.id), title: name,
                                 detail: "repeat of \(f.startedAt.formatted(.dateTime.weekday(.abbreviated).locale(locale)))",
                                 exerciseCount: nil)
        }
    }

    static func dayWord(_ day: String, today: String, locale: Locale, calendar: Calendar) -> String {
        if day == today { return "today" }
        guard let d = calendar.date(from: dateComponents(day)) else { return day }
        return d.formatted(.dateTime.weekday(.abbreviated).locale(locale))
    }

    private static func dateComponents(_ day: String) -> DateComponents {
        let p = day.split(separator: "-").compactMap { Int($0) }
        return p.count == 3 ? DateComponents(year: p[0], month: p[1], day: p[2], hour: 12) : DateComponents()
    }

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
                    if let selected { env.startWorkout(selected) }
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
