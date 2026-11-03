import FitnessCore
import SwiftUI

/// Health-style editor for Summary's Pinned metrics: remove or reorder pinned ones, add from the rest, reset.
struct PinEditorSheet: View {
    @Binding var pinnedRaw: String
    @Environment(\.dismiss) private var dismiss

    private var ids: [String] { SummaryLogic.pinnedIds(pinnedRaw) }
    private func save(_ ids: [String]) { pinnedRaw = SummaryLogic.raw(ids) }

    var body: some View {
        NavigationStack {
            List {
                Section {
                    ForEach(ids, id: \.self) { id in
                        if let metric = MetricCatalog.info(id) { row(metric) }
                    }
                    .onDelete { offsets in
                        var next = ids
                        next.remove(atOffsets: offsets)
                        save(next)
                    }
                    .onMove { from, to in
                        var next = ids
                        next.move(fromOffsets: from, toOffset: to)
                        save(next)
                    }
                } header: {
                    Text("Pinned")
                } footer: {
                    if ids.isEmpty { Text("Nothing pinned. Add metrics from the list below.") }
                }

                let others = MetricCatalog.all.filter { !ids.contains($0.id) }
                if !others.isEmpty {
                    Section("More Metrics") {
                        ForEach(others) { metric in
                            Button {
                                save(ids + [metric.id])
                            } label: {
                                HStack(spacing: 12) {
                                    Image(systemName: "plus.circle.fill")
                                        .font(.title3)
                                        .foregroundStyle(.white, .green)
                                        .accessibilityHidden(true)
                                    row(metric)
                                }
                            }
                            .tint(.primary)
                            .accessibilityLabel("Add \(metric.title)")
                        }
                    }
                }

                Section {
                    Button("Reset to Default") { save(MetricCatalog.defaultPinned) }
                }
            }
            .environment(\.editMode, .constant(.active))
            .navigationTitle("Edit Pinned")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) { Button("Done") { dismiss() } }
            }
        }
        .screenIdentifier(AXID.Summary.pinEditor)
    }

    private func row(_ metric: MetricInfo) -> some View {
        Label {
            Text(metric.title)
        } icon: {
            Image(systemName: metric.symbol).foregroundStyle(metric.domain.color)
        }
    }
}
