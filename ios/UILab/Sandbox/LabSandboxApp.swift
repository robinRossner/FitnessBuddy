import SwiftUI

// Starting point for a new UI experiment. Copy this folder (e.g. to F_Name/), rename the app
// struct, add a target in project.yml, and build screens from Shared/ (LabData, LabKit, LabInsights).
@main
struct LabSandboxApp: App {
    var body: some Scene {
        WindowGroup {
            NavigationStack {
                List {
                    NavigationLink("HRV detail") { MetricDetailView(metric: LabData.metric("hrv")) }
                    Section("Insights") {
                        ForEach(LabData.insights) { LabInsightRow(insight: $0, accent: .teal) }
                    }
                }
                .navigationTitle("UI Sandbox")
            }
        }
    }
}
