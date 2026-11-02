import Foundation
import Testing
@testable import FitnessCore

private func decode<T: Decodable>(_ type: T.Type, _ json: String) throws -> T {
    try CoachCoding.makeDecoder().decode(T.self, from: Data(json.utf8))
}

private let proposalJSON = """

{"id": 7, "status": "pending", "created_at": "2026-10-25T10:00:00Z",
 "trigger": {"type": "missed_session", "title": "Missed Wednesday's easy run"},
 "changes": [{"id": "c1", "param": "running.easy.hr_ceiling_bpm", "label": "HR ceiling", "from": 145, "to": 147,
              "reason": "r", "risk": "low", "valid": true, "status": "proposed"}],
 "schedule_changes": [
   {"id": "s1", "date": "2026-10-30", "action": "move", "target": "intervals", "to_date": "2026-10-31",
    "reason": "Legs heavy", "risk": "low", "valid": true, "status": "proposed", "label": "Move intervals Fri → Sat"},
   {"id": "s2", "date": "2026-11-01", "action": "shorten", "target": "long_run", "distance_km": 9,
    "reason": "Volume", "risk": "medium", "valid": true, "status": "proposed", "label": "Shorten long run to 9 km"},
   {"id": "s3", "date": "2026-10-28", "action": "replace", "target": "easy_run", "new_kind": "intervals",
    "reason": "Catch up", "risk": "high", "valid": false, "validation_error": "Two hard sessions within 48 h",
    "status": "invalid", "label": "Replace easy run with intervals"}]}

"""

private func change(_ id: String, valid: Bool = true) -> CoachChangeDTO {
    CoachChangeDTO(id: id, param: "p." + id, valid: valid, status: valid ? .proposed : .invalid)
}

private func sched(_ id: String, valid: Bool = true, status: CoachChangeStatus = .proposed) -> CoachScheduleChangeDTO {
    CoachScheduleChangeDTO(id: id, action: .move, valid: valid, status: valid ? status : .invalid, label: "Move " + id)
}

private func proposal(changes: [CoachChangeDTO], schedule: [CoachScheduleChangeDTO], status: CoachStatus = .pending) -> CoachProposalDTO {
    CoachProposalDTO(id: "1", status: status, trigger: CoachTriggerDTO(type: .missedSession, title: "t"), changes: changes,
                     scheduleChanges: schedule)
}

@Suite(.tags(.coach, .decoding, .fast))
struct ScheduleChangesDecodingTests {
    @Test func decodesScheduleChanges() throws {
        let p = try decode(CoachProposalDTO.self, proposalJSON)
        #expect(p.id == "7" && p.trigger.type == .missedSession && p.trigger.title == "Missed Wednesday's easy run")
        #expect(p.scheduleChanges.map(\.id) == ["s1", "s2", "s3"])
        let move = p.scheduleChanges[0]
        #expect(move.action == .move && move.target == "intervals" && move.date == "2026-10-30" && move.toDate == "2026-10-31")
        #expect(move.label == "Move intervals Fri → Sat" && move.risk == .low && move.valid && move.status == .proposed)
        #expect(p.scheduleChanges[1].action == .shorten && p.scheduleChanges[1].distanceKm == 9 && p.scheduleChanges[1].risk == .medium)
        let bad = p.scheduleChanges[2]
        #expect(!bad.valid && bad.status == .invalid && bad.newKind == "intervals" && bad.validationError == "Two hard sessions within 48 h")
        #expect(p.isPending && CoachTriggerType.missedSession.symbol == "calendar.badge.exclamationmark")
    }

    @Test func absentOrBrokenScheduleChangesAreEmpty() throws {
        let without = try decode(CoachProposalDTO.self, #"{"id":"1","status":"pending","trigger":{"type":"ask","title":"t"}}"#)
        #expect(without.scheduleChanges.isEmpty)
        let broken = try decode(CoachProposalDTO.self, #"{"id":"1","status":"pending","schedule_changes":"nope"}"#)
        #expect(broken.scheduleChanges.isEmpty && broken.status == .pending)
        // the generated list mixes proposals with and without the key: each decodes to what it carries
        let data = try Fixture.data("coach_proposals")
        let fixture = try CoachCoding.makeDecoder().decode(CoachProposalListDTO.self, from: data)
        let raw = try #require((JSONSerialization.jsonObject(with: data) as? [String: Any])?["items"] as? [[String: Any]])
        #expect(fixture.items.map(\.scheduleChanges.count) == raw.map { ($0["schedule_changes"] as? [Any])?.count ?? 0 })
        #expect(fixture.items.contains { $0.scheduleChanges.isEmpty } && fixture.items.contains { !$0.scheduleChanges.isEmpty })
    }

    @Test func tolerantEnumsAndLabelFallback() throws {
        let c = try decode(CoachScheduleChangeDTO.self, #"{"id":9,"date":"2026-10-30","action":"teleport","risk":"spicy","target":"intervals"}"#)
        #expect(c.id == "9" && c.action == .unknown && c.risk == .unknown && c.valid && c.status == .proposed)
        #expect(c.label.hasPrefix("Change intervals"))
        let moved = try decode(CoachScheduleChangeDTO.self, #"{"id":"a","action":"move","target":"easy_run","date":"2026-10-30","to_date":"2026-10-31"}"#)
        #expect(moved.label == "Move easy run \(DayLabel.weekday("2026-10-30")) → \(DayLabel.weekday("2026-10-31"))")
        let unknownTrigger = try decode(CoachTriggerDTO.self, #"{"type":"something_new","title":"x"}"#)
        #expect(unknownTrigger.type == .unknown)
    }
}

@Suite(.tags(.coach, .decoding, .fast))
struct ApproveIdUnionTests {
    @Test func defaultSelectionCoversBothLists() {
        let p = proposal(changes: [change("c1"), change("c2", valid: false)], schedule: [sched("s1"), sched("s2", valid: false)])
        #expect(CoachLogic.defaultSelection(of: p) == ["c1", "s1"])
        #expect(CoachLogic.selectableCount(p) == 2)
    }

    @Test func approvalIdsAreTheUnionInOrder() {
        let p = proposal(changes: [change("c1"), change("c2"), change("c3", valid: false)],
                         schedule: [sched("s1"), sched("s2"), sched("s3", valid: false)])
        let all = CoachLogic.defaultSelection(of: p)
        #expect(CoachLogic.approvalIds(selection: all, proposal: p) == ["c1", "c2", "s1", "s2"])
        // invalid ids and unknown ids never go out, even when "selected"
        #expect(CoachLogic.approvalIds(selection: ["s3", "c3", "zzz", "s2", "c1"], proposal: p) == ["c1", "s2"])
        #expect(CoachLogic.approvalIds(selection: [], proposal: p).isEmpty)
    }

    @Test func toggleWorksAcrossLists() {
        let p = proposal(changes: [change("c1")], schedule: [sched("s1"), sched("s2", valid: false)])
        var sel = CoachLogic.defaultSelection(of: p)
        sel = CoachLogic.toggled("s1", in: sel, proposal: p)
        #expect(CoachLogic.approvalIds(selection: sel, proposal: p) == ["c1"])
        sel = CoachLogic.toggled("c1", in: sel, proposal: p)
        #expect(CoachLogic.approvalIds(selection: sel, proposal: p).isEmpty)
        sel = CoachLogic.toggled("s2", in: sel, proposal: p)  // invalid: ignored
        #expect(sel.isEmpty)
        sel = CoachLogic.toggled("s1", in: sel, proposal: p)
        #expect(CoachLogic.approvalIds(selection: sel, proposal: p) == ["s1"])
    }

    @Test func scheduleOnlyProposalCanBeApproved() {
        let p = proposal(changes: [], schedule: [sched("s1")])
        #expect(CoachLogic.approvalIds(selection: CoachLogic.defaultSelection(of: p), proposal: p) == ["s1"])
        let partly = proposal(changes: [change("c1")].map { var c = $0; c.status = .applied; return c }, schedule: [sched("s1")], status: .partiallyApplied)
        #expect(partly.isPending)
        let done = proposal(changes: [], schedule: [sched("s1", status: .applied)], status: .partiallyApplied)
        #expect(!done.isPending)
    }

    @Test func approveRequestBodyCarriesTheUnion() throws {
        let p = proposal(changes: [change("c1")], schedule: [sched("s1")])
        let ids = CoachLogic.approvalIds(selection: CoachLogic.defaultSelection(of: p), proposal: p)
        let body = try CoachCoding.makeEncoder().encode(CoachApproveRequest(changeIds: ids))
        let json = try JSONSerialization.jsonObject(with: body) as? [String: Any]
        #expect(json?["change_ids"] as? [String] == ["c1", "s1"])
    }
}
