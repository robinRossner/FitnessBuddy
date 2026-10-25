import SwiftUI

/// Today: "Coach has 1 proposal to review", tap opens it (or the Coach tab when there are several).
struct CoachBanner: View {
    @Environment(AppEnvironment.self) private var env
    let pendingCount: Int
    let latestId: String?

    var body: some View {
        Button { env.openCoach(proposal: pendingCount == 1 ? latestId : nil) } label: {
            HStack(spacing: 12) {
                Image(systemName: "sparkles")
                    .font(.system(size: 16, weight: .semibold))
                    .foregroundStyle(CoachStyle.color)
                    .frame(width: 34, height: 34)
                    .background(CoachStyle.color.opacity(0.15), in: Circle())
                VStack(alignment: .leading, spacing: 1) {
                    Text(CoachLogic.bannerTitle(pendingCount: pendingCount)).font(.subheadline.weight(.semibold)).foregroundStyle(.primary)
                    Text("Tap to review").font(.caption).foregroundStyle(.secondary)
                }
                Spacer(minLength: 0)
                Image(systemName: "chevron.right").font(.footnote.weight(.semibold)).foregroundStyle(.tertiary)
            }
            .padding(12)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(CoachStyle.color.opacity(0.10), in: RoundedRectangle(cornerRadius: 18, style: .continuous))
            .overlay(RoundedRectangle(cornerRadius: 18, style: .continuous).strokeBorder(CoachStyle.color.opacity(0.25), lineWidth: 1))
        }
        .buttonStyle(.plain)
        .accessibilityHint("Opens the Coach tab")
    }
}
