import Foundation
import SwiftData
@testable import FitnessBuddy

final class FixtureToken {}

enum Fixture {
    static func data(_ name: String) throws -> Data {
        let bundle = Bundle(for: FixtureToken.self)
        guard let url = bundle.url(forResource: name, withExtension: "json") else {
            throw CocoaError(.fileNoSuchFile, userInfo: [NSLocalizedDescriptionKey: "missing fixture \(name)"])
        }
        return try Data(contentsOf: url)
    }

    static func decode<T: Decodable>(_ type: T.Type, _ name: String) throws -> T {
        try APICoding.makeDecoder().decode(T.self, from: data(name))
    }
}

@MainActor
func makeContext() throws -> ModelContext {
    ModelContext(try Persistence.makeContainer(inMemory: true))
}

func date(_ s: String) -> Date { ISO8601.parse(s)! }

func sessionDTO(_ id: UUID, updated: String, name: String = "Upper", ended: String? = "2026-10-26T17:00:00.000Z",
                deleted: String? = nil) -> SyncSessionDTO {
    SyncSessionDTO(id: id.uuidString.lowercased(), plannedSessionId: nil, gymId: 4, kind: "upper", name: name,
                   startedAt: date("2026-10-26T16:00:00.000Z"), endedAt: ended.map(date), notes: nil,
                   updatedAt: date(updated), deletedAt: deleted.map(date))
}

func setDTO(_ id: UUID, session: UUID, updated: String, weight: Double = 50, reps: Int = 8, deleted: String? = nil) -> SyncSetDTO {
    SyncSetDTO(id: id.uuidString.lowercased(), sessionId: session.uuidString.lowercased(), variantId: 42, exerciseOrder: 1,
               setOrder: 1, setType: "working", weightKg: weight, reps: reps, durationS: nil,
               completedAt: date("2026-10-26T16:10:00.000Z"), notes: nil, updatedAt: date(updated), deletedAt: deleted.map(date))
}
