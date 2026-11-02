import FitnessCore
import SwiftUI

/// Tag at the top of the app while a sideloaded install is about to stop launching. Settings > About always shows the date.
struct AppExpiryTag: View {
    let expiry: Date

    var body: some View {
        // Re-checked hourly: the app can stay open across midnight.
        TimelineView(.periodic(from: .now, by: 3600)) { context in
            let days = ProvisioningExpiry.daysLeft(until: expiry, from: context.date)
            if days <= ProvisioningExpiry.warningDays {
                Chip(text: "App expires \(ProvisioningExpiry.label(daysLeft: days))",
                     color: days == 0 ? .red : .orange,
                     symbol: "clock.badge.exclamationmark")
                    .padding(.vertical, 2)
            }
        }
    }
}
