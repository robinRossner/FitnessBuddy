import Foundation

// Wire models of the `/api/v1/coach/*` endpoints (docs/api/coach.md once it exists). Decode with `CoachCoding.makeDecoder()`:
// keys are spelled out (config diffs contain free-form keys), enums have an `unknown` fallback and every optional
// block may be missing, so a small backend change never blanks the Coach tab.

enum CoachCoding {
    static func makeDecoder() -> JSONDecoder { APICoding.makeRawKeyDecoder() }

    static func makeEncoder() -> JSONEncoder {
        let e = JSONEncoder()
        e.outputFormatting = [.sortedKeys]
        return e
    }
}

// MARK: - Enums

enum CoachStatus: String, TolerantEnum {
    case awaitingKey = "awaiting_key", queued, running, pending, applied
    case partiallyApplied = "partially_applied", rejected, failed
    case dryRun = "dry_run", unknown
    static var fallback: CoachStatus { .unknown }

    /// Waiting for the advisor (the app polls these).
    var isInFlight: Bool { self == .queued || self == .running }
    var title: String {
        switch self {
        case .awaitingKey: "Waiting for key"
        case .queued: "Queued"
        case .running: "Thinking"
        case .pending: "Needs review"
        case .applied: "Applied"
        case .partiallyApplied: "Partly applied"
        case .rejected: "Rejected"
        case .failed: "Failed"
        case .dryRun: "Dry run"
        case .unknown: "Unknown"
        }
    }
}

enum CoachTriggerType: String, TolerantEnum {
    case liftStall = "lift_stall", ruleConflict = "rule_conflict", runningStagnation = "running_stagnation"
    case dataAnomaly = "data_anomaly", weeklyReview = "weekly_review", ask, unknown
    static var fallback: CoachTriggerType { .unknown }

    var symbol: String {
        switch self {
        case .liftStall: "chart.line.flattrend.xyaxis"
        case .ruleConflict: "arrow.triangle.branch"
        case .runningStagnation: "figure.run"
        case .dataAnomaly: "waveform.path.ecg"
        case .weeklyReview: "calendar"
        case .ask: "bubble.left.and.text.bubble.right"
        case .unknown: "sparkles"
        }
    }
}

enum CoachConfidence: String, TolerantEnum {
    case low, medium, high, unknown
    static var fallback: CoachConfidence { .unknown }
    var title: String { self == .unknown ? "" : rawValue.capitalized + " confidence" }
}

enum CoachRisk: String, TolerantEnum {
    case low, medium, high, unknown
    static var fallback: CoachRisk { .unknown }
    var title: String { self == .unknown ? "Risk ?" : rawValue.capitalized + " risk" }
}

enum CoachChangeStatus: String, TolerantEnum {
    case proposed, applied, rejected, invalid, unknown
    static var fallback: CoachChangeStatus { .unknown }
}

enum CoachAuthor: String, TolerantEnum {
    case engine, user, advisor, seed, unknown
    static var fallback: CoachAuthor { .unknown }

    var symbol: String {
        switch self {
        case .engine: "gearshape.fill"
        case .user: "person.fill"
        case .advisor: "sparkles"
        case .seed: "leaf.fill"
        case .unknown: "questionmark"
        }
    }
    var title: String {
        switch self {
        case .engine: "Engine"
        case .user: "You"
        case .advisor: "Coach"
        case .seed: "Seed"
        case .unknown: "Unknown"
        }
    }
}

enum CoachVerdict: String, TolerantEnum {
    case improved, neutral, worse, unknown
    static var fallback: CoachVerdict { .unknown }
}

enum ApprovalMode: String, TolerantEnum, CaseIterable, Identifiable {
    case manual, autoLowRisk = "auto-low-risk", auto, unknown
    static var fallback: ApprovalMode { .unknown }
    var id: String { rawValue }

    /// The modes the user can pick.
    static var choices: [ApprovalMode] { [.manual, .autoLowRisk, .auto] }

    var title: String {
        switch self {
        case .manual: "Manual"
        case .autoLowRisk: "Auto, low risk"
        case .auto: "Auto"
        case .unknown: "Unknown"
        }
    }
    var explanation: String {
        switch self {
        case .manual: "You approve or reject every proposal."
        case .autoLowRisk: "Low-risk changes apply by themselves, the rest waits for you."
        case .auto: "Every valid change applies by itself. You can roll back in History."
        case .unknown: "Set by the server."
        }
    }
    var symbol: String {
        switch self {
        case .manual: "hand.raised.fill"
        case .autoLowRisk: "shield.lefthalf.filled"
        case .auto: "bolt.fill"
        case .unknown: "questionmark"
        }
    }
}

// MARK: - Helpers

private extension KeyedDecodingContainer {
    /// A String that may arrive as a number.
    func flexString(_ key: Key) -> String? {
        if let s = try? decodeIfPresent(String.self, forKey: key) { return s }
        if let i = try? decodeIfPresent(Int.self, forKey: key) { return String(i) }
        return nil
    }
}

// MARK: - Proposals

struct CoachTriggerRefDTO: Decodable, Sendable, Equatable {
    var kind: String?
    var id: String?

    enum CodingKeys: String, CodingKey { case kind, id }
    init(kind: String? = nil, id: String? = nil) { self.kind = kind; self.id = id }
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        kind = try? c.decodeIfPresent(String.self, forKey: .kind)
        id = c.flexString(.id)
    }
}

struct CoachTriggerDTO: Decodable, Sendable, Equatable {
    var type: CoachTriggerType
    var title: String
    var detail: String?
    var ref: CoachTriggerRefDTO?

    enum CodingKeys: String, CodingKey { case type, title, detail, ref }
    init(type: CoachTriggerType, title: String, detail: String? = nil, ref: CoachTriggerRefDTO? = nil) {
        self.type = type; self.title = title; self.detail = detail; self.ref = ref
    }
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        type = try c.decodeIfPresent(CoachTriggerType.self, forKey: .type) ?? .unknown
        title = try c.decodeIfPresent(String.self, forKey: .title) ?? "Coach proposal"
        detail = try? c.decodeIfPresent(String.self, forKey: .detail)
        ref = try? c.decodeIfPresent(CoachTriggerRefDTO.self, forKey: .ref)
    }
}

struct CoachChangeDTO: Decodable, Sendable, Equatable, Identifiable {
    var id: String
    var param: String
    var label: String
    var from: JSONValue
    var to: JSONValue
    var reason: String
    var risk: CoachRisk
    var valid: Bool
    var validationError: String?
    var status: CoachChangeStatus

    enum CodingKeys: String, CodingKey {
        case id, param, label, from, to, reason, risk, valid, status
        case validationError = "validation_error"
    }
    init(id: String, param: String, label: String? = nil, from: JSONValue = .null, to: JSONValue = .null, reason: String = "",
         risk: CoachRisk = .low, valid: Bool = true, validationError: String? = nil, status: CoachChangeStatus = .proposed) {
        self.id = id; self.param = param; self.label = label ?? param; self.from = from; self.to = to; self.reason = reason
        self.risk = risk; self.valid = valid; self.validationError = validationError; self.status = status
    }
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        param = try c.decodeIfPresent(String.self, forKey: .param) ?? ""
        id = c.flexString(.id) ?? param
        label = try c.decodeIfPresent(String.self, forKey: .label) ?? param
        from = try c.decodeIfPresent(JSONValue.self, forKey: .from) ?? .null
        to = try c.decodeIfPresent(JSONValue.self, forKey: .to) ?? .null
        reason = try c.decodeIfPresent(String.self, forKey: .reason) ?? ""
        risk = try c.decodeIfPresent(CoachRisk.self, forKey: .risk) ?? .unknown
        valid = try c.decodeIfPresent(Bool.self, forKey: .valid) ?? true
        validationError = try? c.decodeIfPresent(String.self, forKey: .validationError)
        status = try c.decodeIfPresent(CoachChangeStatus.self, forKey: .status) ?? (valid ? .proposed : .invalid)
    }

    /// "7–12 → 8–12", with units where the parameter path implies one.
    var changeText: String { CoachValueFormat.change(from: from, to: to, path: param) }
}

struct CoachSessionOverrideDTO: Decodable, Sendable, Equatable, Identifiable {
    var date: String?
    var session: String?
    var change: String
    var reason: String?

    var id: String { [date, session, change].compactMap { $0 }.joined(separator: "|") }

    enum CodingKeys: String, CodingKey { case date, session, change, reason }
    init(date: String? = nil, session: String? = nil, change: String, reason: String? = nil) {
        self.date = date; self.session = session; self.change = change; self.reason = reason
    }
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        date = try? c.decodeIfPresent(String.self, forKey: .date)
        session = try? c.decodeIfPresent(String.self, forKey: .session)
        change = try c.decodeIfPresent(String.self, forKey: .change) ?? ""
        reason = try? c.decodeIfPresent(String.self, forKey: .reason)
    }
}

struct CoachProposalDTO: Decodable, Sendable, Equatable, Identifiable {
    var id: String
    var createdAt: Date?
    var status: CoachStatus
    var trigger: CoachTriggerDTO
    var question: String?
    var diagnosis: String?
    var notesForUser: String?
    var confidence: CoachConfidence?
    var changes: [CoachChangeDTO]
    var sessionOverrides: [CoachSessionOverrideDTO]
    var appliedConfigVersion: String?
    var error: String?
    var costUsd: Double?
    /// Only on `GET /coach/proposals/{id}`.
    var inputSummary: JSONValue?

    /// Waits for a decision: `pending`, or `partially_applied` with changes still `proposed`
    /// (exactly what the server counts in `plan/today.coach`).
    var isPending: Bool {
        switch status {
        case .pending: true
        case .partiallyApplied: changes.contains { $0.status == .proposed && $0.valid }
        default: false
        }
    }

    /// `awaiting_key`, `failed` and `dry_run` proposals can be dismissed (rejected) to clear them.
    var canDismiss: Bool { status == .awaitingKey || status == .failed || status == .dryRun }

    enum CodingKeys: String, CodingKey {
        case id, status, trigger, question, diagnosis, confidence, changes, error
        case createdAt = "created_at", notesForUser = "notes_for_user", sessionOverrides = "session_overrides"
        case appliedConfigVersion = "applied_config_version", costUsd = "cost_usd", inputSummary = "input_summary"
    }

    init(id: String, createdAt: Date? = nil, status: CoachStatus, trigger: CoachTriggerDTO, question: String? = nil,
         diagnosis: String? = nil, notesForUser: String? = nil, confidence: CoachConfidence? = nil, changes: [CoachChangeDTO] = [],
         sessionOverrides: [CoachSessionOverrideDTO] = [], appliedConfigVersion: String? = nil, error: String? = nil,
         costUsd: Double? = nil, inputSummary: JSONValue? = nil) {
        self.id = id; self.createdAt = createdAt; self.status = status; self.trigger = trigger; self.question = question
        self.diagnosis = diagnosis; self.notesForUser = notesForUser; self.confidence = confidence; self.changes = changes
        self.sessionOverrides = sessionOverrides; self.appliedConfigVersion = appliedConfigVersion; self.error = error
        self.costUsd = costUsd; self.inputSummary = inputSummary
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        guard let id = c.flexString(.id) else {
            throw DecodingError.keyNotFound(CodingKeys.id, .init(codingPath: c.codingPath, debugDescription: "id missing"))
        }
        self.id = id
        createdAt = try? c.decodeIfPresent(Date.self, forKey: .createdAt)
        status = try c.decodeIfPresent(CoachStatus.self, forKey: .status) ?? .unknown
        trigger = (try? c.decodeIfPresent(CoachTriggerDTO.self, forKey: .trigger))
            ?? CoachTriggerDTO(type: .unknown, title: "Coach proposal")
        question = try? c.decodeIfPresent(String.self, forKey: .question)
        diagnosis = try? c.decodeIfPresent(String.self, forKey: .diagnosis)
        notesForUser = try? c.decodeIfPresent(String.self, forKey: .notesForUser)
        confidence = try? c.decodeIfPresent(CoachConfidence.self, forKey: .confidence)
        changes = (try? c.decodeIfPresent([CoachChangeDTO].self, forKey: .changes)) ?? []
        sessionOverrides = (try? c.decodeIfPresent([CoachSessionOverrideDTO].self, forKey: .sessionOverrides)) ?? []
        appliedConfigVersion = c.flexString(.appliedConfigVersion)
        error = try? c.decodeIfPresent(String.self, forKey: .error)
        costUsd = try? c.decodeIfPresent(Double.self, forKey: .costUsd)
        inputSummary = try? c.decodeIfPresent(JSONValue.self, forKey: .inputSummary)
    }
}

struct CoachProposalListDTO: Decodable, Sendable, Equatable {
    var items: [CoachProposalDTO]

    enum CodingKeys: String, CodingKey { case items }
    init(items: [CoachProposalDTO]) { self.items = items }
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        items = try c.decodeIfPresent([CoachProposalDTO].self, forKey: .items) ?? []
    }
}

// MARK: - History

struct CoachDiffEntryDTO: Sendable, Equatable, Identifiable {
    var path: String
    var old: JSONValue
    var new: JSONValue
    var id: String { path }

    var changeText: String { CoachValueFormat.change(from: old, to: new, path: path) }
}

struct CoachOutcomeDTO: Decodable, Sendable, Equatable {
    var verdict: CoachVerdict
    var notes: String?
    var metricBefore: JSONValue?
    var metricAfter: JSONValue?

    enum CodingKeys: String, CodingKey {
        case verdict, notes
        case metricBefore = "metric_before", metricAfter = "metric_after"
    }
    init(verdict: CoachVerdict, notes: String? = nil, metricBefore: JSONValue? = nil, metricAfter: JSONValue? = nil) {
        self.verdict = verdict; self.notes = notes; self.metricBefore = metricBefore; self.metricAfter = metricAfter
    }
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        verdict = try c.decodeIfPresent(CoachVerdict.self, forKey: .verdict) ?? .unknown
        notes = try? c.decodeIfPresent(String.self, forKey: .notes)
        metricBefore = try? c.decodeIfPresent(JSONValue.self, forKey: .metricBefore)
        metricAfter = try? c.decodeIfPresent(JSONValue.self, forKey: .metricAfter)
    }
}

extension CoachOutcomeDTO {
    struct MetricRow: Equatable { var name: String; var change: String }

    /// Server metrics are dicts keyed by metric name (`{"bench_press_e1rm_kg": 71.0}`); a bare value is shown unnamed.
    /// "bench press e1rm kg  70 → 71".
    static func metricRows(before: JSONValue?, after: JSONValue?, path: String? = nil) -> [MetricRow] {
        func dict(_ v: JSONValue?) -> [String: JSONValue] { if case .object(let o) = v { o } else { [:] } }
        let b = dict(before), a = dict(after)
        if b.isEmpty && a.isEmpty {
            guard let before, let after, !before.isNull || !after.isNull else { return [] }
            return [MetricRow(name: "Metric", change: CoachValueFormat.change(from: before, to: after, path: path))]
        }
        return Set(b.keys).union(a.keys).sorted().map { key in
            let name = key.replacingOccurrences(of: "_", with: " ")
            return MetricRow(name: name, change: CoachValueFormat.change(from: b[key] ?? .null, to: a[key] ?? .null, path: key))
        }
    }
}

struct CoachVersionDTO: Decodable, Sendable, Equatable, Identifiable {
    var version: String
    var createdAt: Date?
    var author: CoachAuthor
    var reason: String?
    var diff: [CoachDiffEntryDTO]
    var consultationId: String?
    var outcome: CoachOutcomeDTO?
    var active: Bool

    var id: String { version }

    enum CodingKeys: String, CodingKey {
        case version, author, reason, diff, outcome, active
        case createdAt = "created_at", consultationId = "consultation_id"
    }
    private struct Pair: Decodable {
        var old: JSONValue
        var new: JSONValue
        enum CodingKeys: String, CodingKey { case old, new }
        init(from decoder: Decoder) throws {
            let c = try decoder.container(keyedBy: CodingKeys.self)
            old = try c.decodeIfPresent(JSONValue.self, forKey: .old) ?? .null
            new = try c.decodeIfPresent(JSONValue.self, forKey: .new) ?? .null
        }
    }

    init(version: String, createdAt: Date? = nil, author: CoachAuthor, reason: String? = nil, diff: [CoachDiffEntryDTO] = [],
         consultationId: String? = nil, outcome: CoachOutcomeDTO? = nil, active: Bool = false) {
        self.version = version; self.createdAt = createdAt; self.author = author; self.reason = reason; self.diff = diff
        self.consultationId = consultationId; self.outcome = outcome; self.active = active
    }
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        guard let version = c.flexString(.version) else {
            throw DecodingError.keyNotFound(CodingKeys.version, .init(codingPath: c.codingPath, debugDescription: "version missing"))
        }
        self.version = version
        createdAt = try? c.decodeIfPresent(Date.self, forKey: .createdAt)
        author = try c.decodeIfPresent(CoachAuthor.self, forKey: .author) ?? .unknown
        reason = try? c.decodeIfPresent(String.self, forKey: .reason)
        let pairs = (try? c.decodeIfPresent([String: Pair].self, forKey: .diff)) ?? [:]
        diff = pairs.keys.sorted().map { CoachDiffEntryDTO(path: $0, old: pairs[$0]!.old, new: pairs[$0]!.new) }
        consultationId = c.flexString(.consultationId)
        outcome = try? c.decodeIfPresent(CoachOutcomeDTO.self, forKey: .outcome)
        active = try c.decodeIfPresent(Bool.self, forKey: .active) ?? false
    }
}

struct CoachHistoryDTO: Decodable, Sendable, Equatable {
    var versions: [CoachVersionDTO]

    enum CodingKeys: String, CodingKey { case versions }
    init(versions: [CoachVersionDTO]) { self.versions = versions }
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        versions = try c.decodeIfPresent([CoachVersionDTO].self, forKey: .versions) ?? []
    }
}

// MARK: - Settings

struct CoachSettingsDTO: Decodable, Sendable, Equatable {
    var approvalMode: ApprovalMode
    var apiKeyConfigured: Bool
    var dryRun: Bool
    var callsThisWeek: Int
    var maxCallsPerWeek: Int

    enum CodingKeys: String, CodingKey {
        case approvalMode = "approval_mode", apiKeyConfigured = "api_key_configured", dryRun = "dry_run"
        case callsThisWeek = "calls_this_week", maxCallsPerWeek = "max_calls_per_week"
    }
    init(approvalMode: ApprovalMode = .manual, apiKeyConfigured: Bool = false, dryRun: Bool = false,
         callsThisWeek: Int = 0, maxCallsPerWeek: Int = 0) {
        self.approvalMode = approvalMode; self.apiKeyConfigured = apiKeyConfigured; self.dryRun = dryRun
        self.callsThisWeek = callsThisWeek; self.maxCallsPerWeek = maxCallsPerWeek
    }
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        approvalMode = try c.decodeIfPresent(ApprovalMode.self, forKey: .approvalMode) ?? .manual
        apiKeyConfigured = try c.decodeIfPresent(Bool.self, forKey: .apiKeyConfigured) ?? false
        dryRun = try c.decodeIfPresent(Bool.self, forKey: .dryRun) ?? false
        callsThisWeek = (try? c.decodeIfPresent(FlexCount.self, forKey: .callsThisWeek))?.value ?? 0
        maxCallsPerWeek = (try? c.decodeIfPresent(FlexCount.self, forKey: .maxCallsPerWeek))?.value ?? 0
    }
}

/// `coach` block of `GET /plan/today` (decoded with the snake-case decoder, cached with the plan).
struct CoachSummaryDTO: Codable, Sendable, Equatable {
    var pendingCount: Int
    var latestPendingId: String?

    init(pendingCount: Int = 0, latestPendingId: String? = nil) {
        self.pendingCount = pendingCount; self.latestPendingId = latestPendingId
    }
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        pendingCount = (try? c.decodeIfPresent(FlexCount.self, forKey: .pendingCount))?.value ?? 0
        if let s = try? c.decodeIfPresent(String.self, forKey: .latestPendingId) { latestPendingId = s }
        else if let i = try? c.decodeIfPresent(Int.self, forKey: .latestPendingId) { latestPendingId = String(i) }
    }
}

// MARK: - Requests

enum CoachScope: String, Codable, Sendable, CaseIterable {
    case lift, run, week, general
}

struct CoachAskRequest: Encodable, Sendable, Equatable {
    var scope: CoachScope
    var refId: String?
    var question: String
    enum CodingKeys: String, CodingKey { case scope, refId = "ref_id", question }
}

struct CoachApproveRequest: Encodable, Sendable, Equatable {
    /// nil = every valid change.
    var changeIds: [String]?
    enum CodingKeys: String, CodingKey { case changeIds = "change_ids" }
}

struct CoachRejectRequest: Encodable, Sendable, Equatable {
    var note: String?
}

struct CoachSettingsUpdate: Encodable, Sendable, Equatable {
    var approvalMode: ApprovalMode
    enum CodingKeys: String, CodingKey { case approvalMode = "approval_mode" }
}
