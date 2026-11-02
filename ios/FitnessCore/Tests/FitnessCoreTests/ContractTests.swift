import Foundation
import Testing
@testable import FitnessCore

/// The contract gate: every response/request example the backend generates into `docs/api/examples`
/// decodes with the DTO the app uses for that endpoint, with the same decoder the app uses.
/// A new example file without a row here fails `everyExampleHasADecoder`.
@Suite(.tags(.contract, .decoding, .fast))
struct ContractTests {
    private static func api<T: Decodable>(_ type: T.Type) -> @Sendable (Data) throws -> Any {
        { try APICoding.makeDecoder().decode(T.self, from: $0) }
    }

    private static func coach<T: Decodable>(_ type: T.Type) -> @Sendable (Data) throws -> Any {
        { try CoachCoding.makeDecoder().decode(T.self, from: $0) }
    }

    static let table: [(file: String, decode: @Sendable (Data) throws -> Any)] = [
        // plan + sync (docs/api/sync.md)
        ("plan_today", api(PlanTodayDTO.self)),
        ("plan_week", api(PlanWeekDTO.self)),
        ("sync_pull", api(PullResponse.self)),
        ("sync_pull_incremental", api(PullResponse.self)),
        ("sync_push_request", api(PushRequest.self)),
        ("sync_push_response", api(PushResponse.self)),
        ("variant_history", api([VariantHistoryEntryDTO].self)),
        // stats
        ("stats_correlations", api(CorrelationsDTO.self)),
        ("stats_health", api(HealthStatsDTO.self)),
        ("stats_lift_detail", api(LiftDetailDTO.self)),
        ("stats_lifts", api(LiftsStatsDTO.self)),
        ("stats_run_analysis_easy", api(RunAnalysisDTO.self)),
        ("stats_run_analysis_intervals", api(RunAnalysisDTO.self)),
        ("stats_running", api(RunningStatsDTO.self)),
        ("stats_volume_weekly", api(WeeklyVolumeDTO.self)),
        // gyms
        ("gyms_list", api(GymListDTO.self)),
        ("gym_machines", api(GymMachinesDTO.self)),
        // coach (raw-key decoder, as CoachRepository)
        ("coach_proposals", coach(CoachProposalListDTO.self)),
        ("coach_proposal_detail", coach(CoachProposalDTO.self)),
        ("coach_proposal_schedule_detail", coach(CoachProposalDTO.self)),
        ("coach_ask_response", coach(CoachProposalDTO.self)),
        ("coach_ask_schedule_response", coach(CoachProposalDTO.self)),
        ("coach_approve_response", coach(CoachProposalDTO.self)),
        ("coach_history", coach(CoachHistoryDTO.self)),
        ("coach_settings", coach(CoachSettingsDTO.self)),
        ("coach_plan_today", api(PlanTodayDTO.self)),
    ]

    static var exampleFiles: [String] {
        let dir = Bundle.module.resourceURL!  // the examples are processed flat into the bundle root
        let urls = (try? FileManager.default.contentsOfDirectory(at: dir, includingPropertiesForKeys: nil)) ?? []
        return urls.filter { $0.pathExtension == "json" }.map { $0.deletingPathExtension().lastPathComponent }.sorted()
    }

    @Test func everyExampleHasADecoder() {
        let files = Self.exampleFiles
        #expect(files.count >= Self.table.count, "examples folder missing from the test bundle")
        let mapped = Set(Self.table.map(\.file))
        #expect(mapped.count == Self.table.count, "duplicate rows in the contract table")
        #expect(Set(files).subtracting(mapped).sorted() == [], "example files without a DTO mapping")
        #expect(mapped.subtracting(files).sorted() == [], "mapped files missing from docs/api/examples")
    }

    @Test(arguments: table.map(\.file))
    func exampleDecodes(_ file: String) throws {
        let decode = try #require(Self.table.first { $0.file == file }?.decode)
        _ = try decode(Fixture.data(file))
    }
}
