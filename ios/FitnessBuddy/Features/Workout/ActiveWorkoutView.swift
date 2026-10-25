import SwiftData
import SwiftUI

struct ActiveWorkoutView: View {
    @Environment(AppEnvironment.self) private var env
    @Environment(\.modelContext) private var context
    let session: LocalSession
    @Query private var sets: [LocalSet]
    @Query(sort: \CachedVariant.name) private var variants: [CachedVariant]
    @Query private var states: [SyncState]
    @Query private var restOverrides: [LocalRestOverride]

    @State private var showAddExercise = false
    @State private var swapOrder: Int?
    @State private var restPickerOrder: Int?
    @State private var setupTarget: MachineSetupTarget?
    @State private var notesShown: Set<Int> = []
    @State private var confirmFinish = false
    @State private var confirmDiscard = false

    init(session: LocalSession) {
        self.session = session
        let id = session.id
        _sets = Query(filter: #Predicate<LocalSet> { $0.sessionId == id && $0.deletedAt == nil },
                      sort: [SortDescriptor(\.exerciseOrder), SortDescriptor(\.setOrder)])
    }

    private var service: WorkoutService { WorkoutService(context: context) }
    private var variantsById: [Int: CachedVariant] { Dictionary(variants.map { ($0.id, $0) }, uniquingKeysWith: { a, _ in a }) }
    private var restByVariant: [Int: LocalRestOverride] { Dictionary(restOverrides.map { ($0.variantId, $0) }, uniquingKeysWith: { a, _ in a }) }

    /// Rest after a set of this exercise: the user's choice, else the plan's, else the default (see `RestTime.resolve`).
    private func restSeconds(variantId: Int, planned: Int?) -> Int {
        RestTime.resolve(local: restByVariant[variantId], planned: planned, configDefault: states.first?.config?.lifting?.restDefaultS)
    }

    private var dumbbellIncrement: Double? { states.first?.config?.dumbbellIncrementKg }

    private var groups: [(order: Int, sets: [LocalSet])] {
        Dictionary(grouping: sets, by: \.exerciseOrder).map { ($0.key, $0.value) }.sorted { $0.0 < $1.0 }
            .map { (order: $0.0, sets: $0.1.sorted { $0.setOrder < $1.setOrder }) }
    }

    private var completedCount: Int { sets.filter(\.isCompleted).count }
    private var uncompletedCount: Int { sets.count - completedCount }

    var body: some View {
        List {
            Section { header }
            ForEach(groups, id: \.order) { group in
                exerciseSection(group.order, group.sets)
            }
            Section {
                Button { showAddExercise = true } label: {
                    Label("Add exercise", systemImage: "plus.circle.fill").font(.headline).frame(minHeight: 36)
                }
            }
            Section("Workout notes") {
                TextField("Notes", text: Binding(get: { session.notes ?? "" },
                                                  set: { service.setSessionNotes(session, text: $0) }), axis: .vertical)
                    .lineLimit(1...4)
            }
        }
        .listSectionSpacing(14)
        .scrollDismissesKeyboard(.interactively)
        .navigationTitle(session.name)
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .topBarLeading) {
                Menu {
                    Button("Discard workout", systemImage: "trash", role: .destructive) { confirmDiscard = true }
                } label: { Image(systemName: "ellipsis.circle") }
                .accessibilityLabel("Workout options")
            }
            ToolbarItem(placement: .topBarTrailing) {
                Button("Finish") {
                    if uncompletedCount > 0 { confirmFinish = true } else { env.finishWorkout(session) }
                }
                .fontWeight(.semibold)
                .disabled(completedCount == 0)
            }
            ToolbarItemGroup(placement: .keyboard) {
                Spacer()
                Button("Done") { UIApplication.shared.sendAction(#selector(UIResponder.resignFirstResponder), to: nil, from: nil, for: nil) }
            }
        }
        .safeAreaInset(edge: .bottom) { RestTimerBar() }
        .sheet(isPresented: $showAddExercise) {
            ExercisePickerView(title: "Add exercise") { service.addExercise($0, to: session) }
        }
        .sheet(isPresented: Binding(get: { swapOrder != nil }, set: { if !$0 { swapOrder = nil } })) {
            ExercisePickerView(title: "Swap exercise") { variant in
                if let order = swapOrder { service.swapExercise(in: session, order: order, to: variant) }
            }
        }
        .sheet(item: $setupTarget) { MachineSetupSheet(target: $0) }
        .sheet(item: Binding(get: { restPickerOrder.map(RestPickerTarget.init) }, set: { restPickerOrder = $0?.order })) { target in
            restPickerSheet(order: target.order)
        }
        .confirmationDialog("Finish workout?", isPresented: $confirmFinish, titleVisibility: .visible) {
            Button("Finish and drop \(uncompletedCount) unchecked set\(uncompletedCount == 1 ? "" : "s")") { env.finishWorkout(session) }
            Button("Keep going", role: .cancel) {}
        }
        .confirmationDialog("Discard this workout?", isPresented: $confirmDiscard, titleVisibility: .visible) {
            Button("Discard workout", role: .destructive) { env.discardWorkout(session) }
            Button("Keep going", role: .cancel) {}
        }
    }

    // MARK: Header

    private var header: some View {
        HStack(alignment: .top, spacing: 16) {
            VStack(alignment: .leading, spacing: 2) {
                Text("Time").font(.caption).foregroundStyle(.secondary)
                Text(session.startedAt, style: .timer).metricStyle(.title2).foregroundStyle(Domain.lifting.color)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            StatTile(title: "Sets", value: "\(completedCount)/\(sets.count)")
            StatTile(title: "Volume", value: Fmt.number(WorkoutSummary.volume(of: sets).rounded(), maxFraction: 0), unit: "kg")
        }
        .padding(.vertical, 4)
        .accessibilityElement(children: .combine)
    }

    // MARK: Exercise section

    @ViewBuilder
    private func exerciseSection(_ order: Int, _ group: [LocalSet]) -> some View {
        let variant = variantsById[group.first?.variantId ?? -1]
        let note = group.first?.notes
        Section {
            ForEach(Array(group.enumerated()), id: \.element.id) { index, set in
                SetRowView(set: set, label: setLabel(set, in: group, at: index),
                           ladder: variant?.weightLadder ?? [], equipment: variant?.equipmentType ?? "other",
                           dumbbellIncrement: dumbbellIncrement, increments: states.first?.config?.incrementsKg,
                           service: service, session: session) { done in
                    if done {
                        env.restTimer.start(seconds: restSeconds(variantId: set.variantId, planned: set.plannedRestS),
                                            variantId: set.variantId)
                    }
                }
                .listRowInsets(EdgeInsets(top: 8, leading: 10, bottom: 8, trailing: 10))
                .swipeActions(edge: .trailing, allowsFullSwipe: true) {
                    Button(role: .destructive) { service.deleteSet(set) } label: { Label("Delete", systemImage: "trash") }
                }
            }
            if note != nil || notesShown.contains(order) {
                TextField("Exercise note", text: Binding(get: { note ?? "" },
                                                         set: { service.setExerciseNote(in: session, order: order, text: $0) }),
                          axis: .vertical)
                    .font(.subheadline)
                    .lineLimit(1...3)
            }
            Button { service.addSet(to: session, order: order) } label: {
                Label("Add set", systemImage: "plus").font(.subheadline.weight(.semibold)).frame(maxWidth: .infinity, minHeight: 32)
            }
        } header: {
            HStack {
                VStack(alignment: .leading, spacing: 1) {
                    Text((group.first?.variantName.isEmpty == false ? group.first!.variantName : variant?.name ?? "Exercise")
                        .withoutGymSuffix)
                        .font(.headline).foregroundStyle(.primary)
                    if group.first?.isBaseline == true {
                        Text(SetupRules.baselineNote).font(.caption).foregroundStyle(Domain.lifting.color).textCase(nil)
                    }
                    if group.first?.needsSetup == true, let first = group.first {
                        Button { setupTarget = MachineSetupTarget(variantId: first.variantId, name: first.variantName) } label: {
                            Label("Set up machine", systemImage: "wrench.and.screwdriver").font(.caption.weight(.semibold))
                        }
                        .buttonStyle(.bordered).tint(.orange).controlSize(.small)
                        .disabled(!env.gyms.canWrite)
                    }
                    HStack(spacing: 8) {
                        if let lo = variant?.repRangeLow, let hi = variant?.repRangeHigh {
                            Text("Target \(lo)–\(hi) reps").font(.caption).foregroundStyle(.secondary)
                        }
                        if let first = group.first {
                            let seconds = restSeconds(variantId: first.variantId, planned: first.plannedRestS)
                            Button { restPickerOrder = order } label: {
                                HStack(spacing: 3) {
                                    Image(systemName: "timer")
                                    Text(RestTime.format(seconds)).monospacedDigit()
                                }
                                .font(.caption).foregroundStyle(.secondary)
                                .padding(.horizontal, 7).padding(.vertical, 2)
                                .background(Color.secondary.opacity(0.15), in: Capsule())
                            }
                            .buttonStyle(.plain)
                            .accessibilityLabel("Rest time \(RestTime.format(seconds))")
                            .accessibilityHint("Opens the rest time picker")
                        }
                    }
                }
                Spacer()
                Menu {
                    Button("Swap exercise", systemImage: "arrow.triangle.swap") { swapOrder = order }
                    Button("Rest time", systemImage: "timer") { restPickerOrder = order }
                    Button("Add note", systemImage: "note.text") { notesShown.insert(order) }
                    Button("Skip exercise", systemImage: "forward.end", role: .destructive) {
                        service.skipExercise(in: session, order: order)
                    }
                } label: {
                    Image(systemName: "ellipsis.circle").font(.title3).frame(width: 44, height: 44).contentShape(Rectangle())
                }
                .accessibilityLabel("Exercise options")
            }
            .textCase(nil)
        }
    }

    @ViewBuilder
    private func restPickerSheet(order: Int) -> some View {
        if let first = groups.first(where: { $0.order == order })?.sets.first {
            let variantId = first.variantId
            let defaultSeconds = states.first?.config?.lifting?.restDefaultS ?? RestTime.fallbackSeconds
            let name = (first.variantName.isEmpty ? variantsById[variantId]?.name ?? "Exercise" : first.variantName).withoutGymSuffix
            let resolved = restSeconds(variantId: variantId, planned: first.plannedRestS)
            RestTimePickerSheet(exerciseName: name, defaultSeconds: defaultSeconds,
                                current: resolved == defaultSeconds ? nil : resolved) { choice in
                RestSettings.set(variantId: variantId, seconds: choice, in: context)
                // a rest that is running for this exercise follows the new time right away
                env.restTimer.retarget(variantId: variantId, to: choice ?? defaultSeconds)
                env.sync.request(.restTimeChanged)
            }
        }
    }

    private func setLabel(_ set: LocalSet, in group: [LocalSet], at index: Int) -> String {
        switch set.setType {
        case "warmup": return "W"
        case "drop": return "D"
        case "failure": return "F"
        default:
            let n = group[...index].filter { $0.setType != "warmup" }.count
            return "\(n)"
        }
    }
}

private struct RestPickerTarget: Identifiable {
    let order: Int
    var id: Int { order }
}

// MARK: - Rest time picker

/// "Rest time" of one exercise: Default (2:00) or 1:00 to 5:00 in 15 s steps. Saving stores it for every future
/// workout with this exercise (see `RestSettings`).
struct RestTimePickerSheet: View {
    @Environment(\.dismiss) private var dismiss
    let exerciseName: String
    let defaultSeconds: Int
    /// The exercise's own time; nil = it uses the default.
    let current: Int?
    let onSave: (Int?) -> Void
    @State private var choice: Int?

    init(exerciseName: String, defaultSeconds: Int, current: Int?, onSave: @escaping (Int?) -> Void) {
        self.exerciseName = exerciseName
        self.defaultSeconds = defaultSeconds
        self.current = current
        self.onSave = onSave
        _choice = State(initialValue: current)
    }

    var body: some View {
        NavigationStack {
            VStack(spacing: 6) {
                Text(exerciseName).font(.subheadline).foregroundStyle(.secondary)
                Picker("Rest time", selection: $choice) {
                    Text(RestTime.defaultLabel(default: defaultSeconds)).tag(Int?.none)
                    ForEach(RestTime.pickerChoices(current: current), id: \.self) { seconds in
                        Text(RestTime.format(seconds)).tag(Int?.some(seconds))
                    }
                }
                .pickerStyle(.wheel)
                .frame(height: 190)
                .accessibilityIdentifier("restTimePicker")
                Text("Applies to every workout with this exercise.").font(.footnote).foregroundStyle(.secondary)
                Spacer(minLength: 0)
            }
            .padding(.horizontal)
            .navigationTitle("Rest time")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Save") { onSave(choice); dismiss() }.fontWeight(.semibold)
                }
            }
        }
        .presentationDetents([.medium])
    }
}

// MARK: - Rest timer bar

struct RestTimerBar: View {
    @Environment(AppEnvironment.self) private var env

    var body: some View {
        let timer = env.restTimer
        if timer.isRunning {
            TimelineView(.periodic(from: .now, by: 0.5)) { ctx in
                let remaining = timer.remaining(at: ctx.date)
                HStack(spacing: 12) {
                    Image(systemName: "timer").foregroundStyle(Domain.lifting.color)
                    VStack(alignment: .leading, spacing: 4) {
                        Text("Rest \(Fmt.duration(remaining))").metricStyle(.title3, weight: .semibold)
                        ProgressView(value: timer.totalSeconds > 0 ? 1 - remaining / timer.totalSeconds : 1)
                            .tint(Domain.lifting.color)
                    }
                    Button { timer.adjust(by: -15) } label: { Text("−15").frame(minWidth: 36, minHeight: 36) }
                        .buttonStyle(.bordered)
                        .accessibilityLabel("Subtract 15 seconds")
                    Button { timer.adjust(by: 15) } label: { Text("+15").frame(minWidth: 36, minHeight: 36) }
                        .buttonStyle(.bordered)
                        .accessibilityLabel("Add 15 seconds")
                    Button { timer.cancel() } label: { Image(systemName: "xmark").frame(minWidth: 28, minHeight: 36) }
                        .buttonStyle(.bordered).tint(.secondary)
                        .accessibilityLabel("Skip rest")
                }
                .padding(12)
                .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 20, style: .continuous))
                .padding(.horizontal, 12)
                .padding(.bottom, 4)
            }
            .transition(.move(edge: .bottom).combined(with: .opacity))
        }
    }
}

// MARK: - Exercise picker

struct ExercisePickerView: View {
    @Environment(\.dismiss) private var dismiss
    @Environment(AppEnvironment.self) private var env
    @Query(filter: #Predicate<CachedVariant> { $0.active && $0.deletedAt == nil }, sort: \CachedVariant.name) private var variants: [CachedVariant]
    let title: String
    let onPick: (CachedVariant) -> Void
    @State private var search = ""

    private var filtered: [CachedVariant] {
        let gym = env.settings.currentGymId
        let scoped = variants.filter { gym == nil || $0.gymId == nil || $0.gymId == gym }
        guard !search.isEmpty else { return scoped }
        return scoped.filter { $0.name.localizedCaseInsensitiveContains(search) }
    }

    var body: some View {
        NavigationStack {
            List(filtered, id: \.id) { v in
                Button {
                    onPick(v)
                    dismiss()
                } label: {
                    VStack(alignment: .leading, spacing: 2) {
                        Text(v.name.withoutGymSuffix).font(.body)
                        Text(v.equipmentType.replacingOccurrences(of: "_", with: " ").capitalized)
                            .font(.caption).foregroundStyle(.secondary)
                    }
                    .frame(minHeight: 44, alignment: .leading)
                    .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
            }
            .overlay {
                if variants.isEmpty {
                    ContentUnavailableView("No exercises yet", systemImage: "arrow.triangle.2.circlepath",
                                           description: Text("Sync with the backend to load your exercise library."))
                } else if filtered.isEmpty {
                    ContentUnavailableView.search(text: search)
                }
            }
            .searchable(text: $search, placement: .navigationBarDrawer(displayMode: .always))
            .navigationTitle(title)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } } }
        }
    }
}
