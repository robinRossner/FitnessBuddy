import SwiftUI

// MARK: - Week strip

struct WeekStrip: View {
    let planned: [CachedPlannedSession]
    let sessions: [LocalSession]
    let runDays: Set<String>
    let today: String

    var body: some View {
        let days = AppClock.weekDays(containing: AppClock.now)
        let cells = WeekStripModel.cells(
            days: days,
            planned: planned.map { PlannedDayRef(id: $0.id, date: $0.date, kind: $0.kind, status: $0.status) },
            finished: sessions.filter { $0.endedAt != nil && $0.deletedAt == nil }
                .map { FinishedDayRef(plannedId: $0.plannedSessionId, day: AppClock.dayString($0.startedAt), kind: $0.kind) },
            runDays: runDays, today: today)
        Card {
            CardHeader(title: "Plan", symbol: "calendar", color: .secondary,
                       info: "Faint days are your usual pattern where nothing is planned yet.")
            HStack(spacing: 4) {
                ForEach(cells) { DayCell(cell: $0, isToday: $0.day == today) }
            }
        }
    }
}

private struct DayCell: View {
    let cell: WeekCellModel
    let isToday: Bool

    private var weekday: String {
        guard let d = AppClock.date(fromDay: cell.day) else { return "" }
        return d.formatted(.dateTime.weekday(.narrow))
    }
    private var color: Color { cell.state == .rest ? .gray : Domain.forKind(cell.kind).color }
    private var faint: Bool { cell.state == .expected }
    private var stateWord: String {
        switch cell.state {
        case .done: "done"
        case .planned: "planned"
        case .missed: "missed"
        case .expected: "usual pattern, not planned yet"
        case .rest: "rest"
        }
    }

    var body: some View {
        VStack(spacing: 6) {
            Text(weekday).font(.caption2.weight(.semibold)).foregroundStyle(isToday ? Color.white : .secondary)
            ZStack(alignment: .bottomTrailing) {
                Image(systemName: PlanFormat.symbol(forKind: cell.kind))
                    .font(.body)
                    .foregroundStyle(isToday ? Color.white : (cell.state == .rest ? Color.secondary.opacity(0.5) : color))
                    .frame(width: 32, height: 32)
                    .background(isToday ? Color.white.opacity(0.25) : color.opacity(cell.state == .rest ? 0.14 : (faint ? 0.05 : 0.14)), in: Circle())
                    .overlay {
                        if faint || cell.state == .missed {
                            Circle().strokeBorder(isToday ? Color.white.opacity(0.7) : (cell.state == .missed ? Color.orange.opacity(0.7) : color.opacity(0.45)),
                                                  style: StrokeStyle(lineWidth: 1, dash: cell.state == .missed ? [] : [3, 3]))
                        }
                    }
                if cell.state == .done {
                    Image(systemName: "checkmark.circle.fill")
                        .font(.caption)
                        .foregroundStyle(.white, .green)
                        .offset(x: 4, y: 4)
                } else if cell.state == .missed {
                    Image(systemName: "minus.circle.fill")
                        .font(.caption)
                        .foregroundStyle(.white, Color.orange.opacity(0.8))
                        .offset(x: 4, y: 4)
                }
            }
            Text(PlanFormat.shortName(forKind: cell.kind))
                .font(.caption2.weight(.medium))
                .foregroundStyle(isToday ? Color.white : .secondary)
                .lineLimit(1).minimumScaleFactor(0.7)
        }
        .opacity(faint && !isToday ? 0.7 : 1)
        .padding(.vertical, 8)
        .frame(maxWidth: .infinity)
        .background(isToday ? Color.accentColor : .clear, in: RoundedRectangle(cornerRadius: 14, style: .continuous))
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("\(cell.day), \(PlanFormat.shortName(forKind: cell.kind)), \(stateWord)")
    }
}
