import FitnessCore
import SwiftData
import SwiftUI

// MARK: - Exercise picker

/// Add / swap an exercise: every exercise in sections by body region. Exercises without a variant at the current gym
/// (a new gym, or never done) are set up on the spot; "New" creates an exercise that isn't in the library yet.
struct ExercisePickerView: View {
    @Environment(\.dismiss) private var dismiss
    @Environment(AppEnvironment.self) private var env
    @Query(sort: \CachedExercise.name) private var exercises: [CachedExercise]
    @Query(sort: \CachedVariant.name) private var variants: [CachedVariant]
    let title: String
    let onPick: (CachedVariant) -> Void
    @State private var search = ""
    @State private var setupRow: ExercisePickRow?
    @State private var showNew = false

    private var gymId: Int? { env.settings.currentGymId }
    private var groups: [ExercisePickGroup] {
        ExerciseCatalog.groups(exercises: exercises, variants: variants, gymId: gymId, search: search)
    }

    var body: some View {
        NavigationStack {
            List {
                ForEach(groups) { group in
                    Section(group.region) {
                        ForEach(group.rows) { row in
                            Button { pick(row) } label: { rowLabel(row) }
                                .buttonStyle(.plain)
                        }
                    }
                }
                Section {
                    Button { showNew = true } label: {
                        Label(search.isEmpty ? "New exercise" : "New exercise \u{201C}\(search)\u{201D}", systemImage: "plus.circle.fill")
                    }
                }
            }
            .overlay {
                if variants.isEmpty && exercises.isEmpty {
                    LoadableContent(.empty(title: "No exercises yet", symbol: "arrow.triangle.2.circlepath",
                                           message: "Sync with the backend to load your exercise library."), style: .screen)
                }
            }
            .searchable(text: $search, placement: .navigationBarDrawer(displayMode: .always))
            .navigationTitle(title)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } }
                ToolbarItem(placement: .primaryAction) {
                    Button { showNew = true } label: { Label("New exercise", systemImage: "plus") }
                }
            }
            .sheet(item: $setupRow) { row in
                ExerciseSetupSheet(row: row,
                                   defaultEquipment: exercises.first { $0.id == row.exerciseId }?.defaultEquipment) { variant in
                    finish(variant)
                }
            }
            .sheet(isPresented: $showNew) {
                NewExerciseSheet(initialName: search) { finish($0) }
            }
        }
        .screenIdentifier(AXID.Workout.exercisePicker)
        .stableForScreenshots()
        #if DEBUG
        .task { await openRequestedSheet() }
        #endif
    }

    #if DEBUG
    /// `-uiSheet newExercise|exerciseSetup` (see `WorkoutUIHooks`): opens that nested sheet once.
    private func openRequestedSheet() async {
        guard let name = WorkoutUIHooks.takeSheet(["newExercise", "exerciseSetup"], slot: "picker") else { return }
        try? await Task.sleep(for: .milliseconds(400))
        if name == "newExercise" { showNew = true } else { setupRow = groups.lazy.flatMap(\.rows).first { $0.variantId == nil } }
    }
    #endif

    private func rowLabel(_ row: ExercisePickRow) -> some View {
        HStack {
            VStack(alignment: .leading, spacing: 2) {
                Text(row.title).font(.body)
                Text(row.variantId == nil ? "Not set up here yet" : ExerciseOptions.equipmentLabel(row.equipmentType))
                    .font(.caption).foregroundStyle(.secondary)
            }
            Spacer(minLength: 8)
            if row.variantId == nil {
                Image(systemName: "plus.circle").foregroundStyle(Domain.lifting.color).accessibilityHidden(true)
            }
        }
        .frame(minHeight: 44, alignment: .leading)
        .contentShape(Rectangle())
        .accessibilityHint(Text(row.variantId == nil ? "Sets it up for this gym first" : "Adds it to the workout"))
    }

    private func pick(_ row: ExercisePickRow) {
        if let id = row.variantId, let variant = variants.first(where: { $0.id == id }) {
            finish(variant)
        } else {
            setupRow = row
        }
    }

    private func finish(_ variant: CachedVariant) {
        onPick(variant)
        dismiss()
    }
}

// MARK: - Set up an exercise at this gym

/// An exercise without a variant here: choose the equipment, the server creates the variant (a machine at the current
/// gym, or a generic barbell / dumbbell / bodyweight one).
struct ExerciseSetupSheet: View {
    @Environment(AppEnvironment.self) private var env
    @Environment(\.dismiss) private var dismiss
    let row: ExercisePickRow
    let onDone: (CachedVariant) -> Void
    @State private var equipment: String
    @State private var saving = false
    @State private var errorText: String?

    init(row: ExercisePickRow, defaultEquipment: String?, onDone: @escaping (CachedVariant) -> Void) {
        self.row = row
        self.onDone = onDone
        _equipment = State(initialValue: defaultEquipment ?? "machine_stack")
    }

    private var gymName: String { env.gyms.gym(env.settings.currentGymId)?.name ?? "this gym" }

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    Picker("Type", selection: $equipment) {
                        ForEach(ExerciseOptions.equipment, id: \.key) { Text($0.label).tag($0.key) }
                    }
                    .pickerStyle(.inline)
                    .labelsHidden()
                } header: {
                    Text("What do you use for \(row.title)?")
                } footer: {
                    Text(ExerciseOptions.gymBound.contains(equipment)
                         ? "Saved as a machine at \(gymName), with its own progression. Add its weight steps later in Settings > Gym."
                         : "Saved for every gym.")
                }
                SaveStatusSection(errorText: errorText, canWrite: env.exercises.canWrite)
            }
            .navigationTitle("Set up exercise")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } }
                ToolbarItem(placement: .confirmationAction) {
                    Button { Task { await save() } } label: {
                        if saving { ProgressView() } else { Text("Add").fontWeight(.semibold) }
                    }
                    .disabled(saving || !env.exercises.canWrite)
                }
            }
            .interactiveDismissDisabled(saving)
        }
        .screenIdentifier(AXID.Workout.exerciseSetup)
        .stableForScreenshots()
        .presentationDetents([.large])
    }

    private func save() async {
        saving = true
        defer { saving = false }
        do {
            let variant = try await env.exercises.addVariant(exerciseId: row.exerciseId, equipmentType: equipment,
                                                             gymId: env.settings.currentGymId)
            env.sync.request(.manual)
            onDone(variant)
        } catch {
            errorText = error.localizedDescription
        }
    }
}

// MARK: - New exercise

struct NewExerciseSheet: View {
    @Environment(AppEnvironment.self) private var env
    @Environment(\.dismiss) private var dismiss
    let onDone: (CachedVariant) -> Void
    @State private var draft: NewExerciseDraft
    @State private var saving = false
    @State private var errorText: String?

    init(initialName: String = "", onDone: @escaping (CachedVariant) -> Void) {
        self.onDone = onDone
        var draft = NewExerciseDraft()
        draft.name = initialName
        _draft = State(initialValue: draft)
    }

    private var gymName: String { env.gyms.gym(env.settings.currentGymId)?.name ?? "this gym" }

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    TextField("Name (e.g. Lat Pulldown)", text: $draft.name)
                        .textInputAutocapitalization(.words)
                    MusclePicker(selection: $draft.muscleGroup)
                    Picker("Type", selection: $draft.equipmentType) {
                        ForEach(ExerciseOptions.equipment, id: \.key) { Text($0.label).tag($0.key) }
                    }
                    ProgressionPicker(selection: $draft.progression, options: Progression.allCases)
                    if draft.progression == .load { IncrementPicker(selection: $draft.incrementKg) }
                } footer: {
                    Text(ExerciseOptions.gymBound.contains(draft.equipmentType)
                         ? "Machines are saved for \(gymName). At another gym you set it up again (one tap in the exercise list)."
                         : "Saved for every gym.")
                }
                SaveStatusSection(errorText: errorText, canWrite: env.exercises.canWrite)
            }
            .navigationTitle("New exercise")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } }
                ToolbarItem(placement: .confirmationAction) {
                    Button { Task { await save() } } label: {
                        if saving { ProgressView() } else { Text("Add").fontWeight(.semibold) }
                    }
                    .disabled(saving || !draft.isValid || !env.exercises.canWrite)
                }
            }
            .interactiveDismissDisabled(saving)
        }
        .screenIdentifier(AXID.Workout.newExercise)
        .stableForScreenshots()
    }

    private func save() async {
        saving = true
        defer { saving = false }
        do {
            let variant = try await env.exercises.create(draft, gymId: env.settings.currentGymId)
            env.sync.request(.manual)
            onDone(variant)
        } catch {
            errorText = error.localizedDescription
        }
    }
}

// MARK: - Edit exercise

/// Name and muscle (whole exercise), type and load step (this variant). Rest time has its own picker.
struct ExerciseEditSheet: View {
    @Environment(AppEnvironment.self) private var env
    @Environment(\.dismiss) private var dismiss
    let variant: CachedVariant
    let exercise: CachedExercise?
    @State private var name: String
    @State private var muscle: String
    @State private var equipment: String
    @State private var increment: Double?
    @State private var progression: Progression
    @State private var saving = false
    @State private var errorText: String?

    init(variant: CachedVariant, exercise: CachedExercise?) {
        self.variant = variant
        self.exercise = exercise
        _name = State(initialValue: ExerciseOptions.editableName(variantName: variant.name, equipmentType: variant.equipmentType))
        _muscle = State(initialValue: exercise?.muscleGroup ?? "chest")
        _equipment = State(initialValue: variant.equipmentType)
        _increment = State(initialValue: variant.incrementKg)
        _progression = State(initialValue: exercise?.progression ?? .load)
    }

    private var initialName: String {
        ExerciseOptions.editableName(variantName: variant.name, equipmentType: variant.equipmentType)
    }
    private var nameChanged: Bool {
        exercise != nil && ExerciseOptions.cleanName(name) != ExerciseOptions.cleanName(initialName)
    }
    private var nameValid: Bool { !ExerciseOptions.cleanName(name).isEmpty }

    private var progressionChanged: Bool { exercise != nil && progression != exercise?.progression }

    private var changed: Bool {
        nameChanged || (exercise != nil && muscle != exercise?.muscleGroup) || equipment != variant.equipmentType
            || increment != variant.incrementKg || progressionChanged
    }

    private var nameFooter: String {
        let shown = ExerciseOptions.cleanName(name)
        let label = ExerciseOptions.nameLabel(equipment).map { " (\($0))" } ?? ""
        return nameChanged
            ? "Shown as \u{201C}\(shown)\(label)\u{201D}, for every setup of this exercise."
            : "Renames every setup of this exercise (gym machines, barbell, dumbbell). History stays."
    }

    var body: some View {
        NavigationStack {
            Form {
                if exercise != nil {
                    Section {
                        TextField("Name", text: $name)
                            .textInputAutocapitalization(.words)
                    } footer: {
                        Text(nameFooter)
                    }
                }
                Section {
                    if exercise != nil { MusclePicker(selection: $muscle) }
                    Picker("Type", selection: $equipment) {
                        ForEach(ExerciseOptions.equipment, id: \.key) { Text($0.label).tag($0.key) }
                    }
                    if let exercise {
                        if exercise.progression == .time {
                            LabeledContent("Progression", value: Progression.time.label)
                        } else {
                            ProgressionPicker(selection: $progression, options: Progression.editable)
                        }
                    }
                    if progression != .reps { IncrementPicker(selection: $increment) }
                } header: {
                    Text(variant.name.withoutGymSuffix)
                } footer: {
                    Text(progression == .reps
                         ? "Reps only: the plan never suggests weight. The rep target goes up by one after each clean session, then holds at the top of the range."
                         : increment == nil
                         ? "Default steps: the machine's weight steps if set (Settings > Gym), else the usual step for the type."
                         : "+ / − and the progression move by \(Fmt.number(increment ?? 0)) kg.")
                }
                SaveStatusSection(errorText: errorText, canWrite: env.exercises.canWrite)
            }
            .navigationTitle("Edit exercise")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } }
                ToolbarItem(placement: .confirmationAction) {
                    Button { Task { await save() } } label: {
                        if saving { ProgressView() } else { Text("Save").fontWeight(.semibold) }
                    }
                    .disabled(saving || !changed || !nameValid || !env.exercises.canWrite)
                }
            }
            .interactiveDismissDisabled(saving)
        }
        .screenIdentifier(AXID.Workout.exerciseEdit)
        .stableForScreenshots()
    }

    private func save() async {
        saving = true
        defer { saving = false }
        do {
            if let exercise, muscle != exercise.muscleGroup {
                try await env.exercises.setMuscle(exerciseId: exercise.id, muscle: muscle)
            }
            if let exercise, progressionChanged {
                try await env.exercises.setProgression(exerciseId: exercise.id, to: progression)
            }
            if equipment != variant.equipmentType || increment != variant.incrementKg {
                try await env.exercises.setVariant(variant.id, equipmentType: equipment, incrementKg: increment)
            }
            // last, so the new name carries the (possibly new) type label
            if let exercise, nameChanged {
                try await env.exercises.rename(exerciseId: exercise.id, to: name)
            }
            env.sync.request(.manual)
            dismiss()
        } catch {
            errorText = error.localizedDescription
        }
    }
}

// MARK: - Shared form pieces

struct MusclePicker: View {
    @Binding var selection: String

    var body: some View {
        Picker("Muscle", selection: $selection) {
            ForEach(ExerciseOptions.regions, id: \.self) { region in
                let muscles = ExerciseOptions.muscles.filter { $0.region == region }
                if !muscles.isEmpty {
                    Section(region) {
                        ForEach(muscles, id: \.key) { Text($0.label).tag($0.key) }
                    }
                }
            }
        }
    }
}

struct ProgressionPicker: View {
    @Binding var selection: Progression
    let options: [Progression]

    var body: some View {
        Picker("Progression", selection: $selection) {
            ForEach(options, id: \.self) { Text($0.label).tag($0) }
        }
    }
}

struct IncrementPicker: View {
    @Binding var selection: Double?

    var body: some View {
        Picker("Weight step", selection: $selection) {
            Text("Default").tag(Double?.none)
            ForEach(choices, id: \.self) { kg in
                Text("\(Fmt.number(kg)) kg").tag(Double?.some(kg))
            }
        }
    }

    /// The fixed choices plus a stored value that isn't one of them.
    private var choices: [Double] {
        var out = ExerciseOptions.increments
        if let selection, !out.contains(selection) { out.append(selection); out.sort() }
        return out
    }
}

struct SaveStatusSection: View {
    let errorText: String?
    let canWrite: Bool

    var body: some View {
        if errorText != nil || !canWrite {
            Section {
                if let errorText { Label(errorText, systemImage: "xmark.octagon.fill").font(.footnote).foregroundStyle(.red) }
                if !canWrite {
                    Label("Needs a connection. Exercises are saved on the server.", systemImage: "icloud.slash")
                        .font(.footnote).foregroundStyle(.secondary)
                }
            }
        }
    }
}
