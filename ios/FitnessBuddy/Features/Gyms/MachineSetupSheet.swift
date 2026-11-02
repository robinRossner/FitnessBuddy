import FitnessCore
import SwiftData
import SwiftUI

/// What the "Set up machine" button of a plan exercise knows about it.
struct MachineSetupTarget: Identifiable, Equatable {
    let id = UUID()
    var exerciseId: Int?
    var variantId: Int?
    var slug: String?
    var name: String

    init(exercise ex: PlannedExerciseDTO) {
        exerciseId = ex.exerciseId; variantId = ex.variantId; slug = ex.exercise; name = ex.name.withoutGymSuffix
    }

    init(variantId: Int, name: String) {
        self.variantId = variantId; self.name = name.withoutGymSuffix
    }
}

/// Opens the machine editor for a plan exercise at the current gym: finds the exercise in the gym's machine list,
/// then shows the editor. After saving, the repository re-plans (sync), so Today and the workout pick it up.
struct MachineSetupSheet: View {
    @Environment(AppEnvironment.self) private var env
    @Environment(\.modelContext) private var context
    @Environment(\.dismiss) private var dismiss
    let target: MachineSetupTarget
    @State private var item: MachineItemDTO?
    @State private var loading = true
    @State private var message: String?

    private var gymId: Int? { env.gyms.currentGymId }
    private var gymName: String { env.gyms.gym(gymId)?.name ?? env.gyms.machineList(gymId ?? -1)?.gym.name ?? "your gym" }

    var body: some View {
        if let item, let gymId {
            MachineEditorSheet(gymId: gymId, gymName: gymName, item: item)
        } else {
            NavigationStack {
                VStack(spacing: 12) {
                    LoadableContent(loading ? .loading() : .empty(title: "Can't open the machine", symbol: "icloud.slash",
                                                                  message: message ?? "Open Settings > Gym to set it up."),
                                    style: .screen)
                }
                .frame(maxWidth: .infinity, maxHeight: .infinity)
                .navigationTitle(target.name)
                .navigationBarTitleDisplayMode(.inline)
                .toolbar { ToolbarItem(placement: .cancellationAction) { Button("Close") { dismiss() } } }
            }
            .task { await load() }
        }
    }

    private func load() async {
        loading = true
        defer { loading = false }
        if env.gyms.list == nil { await env.gyms.loadList() }
        guard let gymId = env.gyms.currentGymId else { message = "No current gym. Choose one in Settings > Gym."; return }
        await env.gyms.loadMachines(gymId: gymId)
        guard let items = env.gyms.machineList(gymId)?.items else {
            message = env.gyms.failure ?? "Needs a connection."
            return
        }
        let variantExercise = target.variantId.flatMap { id in
            try? context.fetch(FetchDescriptor<CachedVariant>(predicate: #Predicate { $0.id == id })).first?.exerciseId
        }
        item = MachineLogic.match(items, exerciseId: target.exerciseId ?? variantExercise, slug: target.slug, name: target.name)
        if item == nil { message = "\(target.name) isn't in this gym's machine list yet." }
    }
}
