import AuthenticationServices
import Foundation
import SwiftData
import UIKit

@MainActor
final class AppModel: ObservableObject {
    @Published var member: MemberDTO?
    @Published var stays: [StayCluster] = []
    @Published var home: HomeConfirmation?
    @Published var homeProposal: HomeProposal?
    @Published var trips: [TripSummaryDTO] = []
    @Published var suggestions: [MergeSuggestionDTO] = []
    @Published var status = ""
    @Published var busy = false
    let api = APIClient()

    func restore(context: ModelContext) {
        if let token = KeychainStore.read() {
            api.token = token
        }
        stays = JournalCache.loadStays(context: context)
        home = JournalCache.loadHome(context: context)
    }

    func signIn(identityToken: String, displayName: String?) async {
        await run("Signed in") {
            let response = try await self.api.signInWithApple(identityToken: identityToken, displayName: displayName)
            self.keep(response)
        }
    }

    func devSignIn(name: String) async {
        await run("Signed in on this network") {
            let response = try await self.api.devSignIn(name: name)
            self.keep(response)
        }
    }

    func signOut() {
        api.token = nil
        member = nil
        KeychainStore.clear()
        status = "Signed out"
    }

    func scanLibrary(context: ModelContext) async {
        let allowed = await PhotoLibraryScanner.requestAccess()
        guard allowed else {
            status = "Photo access is needed to read locations. Originals stay in Photos."
            return
        }
        busy = true
        status = "Reading photo locations"
        let points = await Task.detached(priority: .userInitiated) { PhotoLibraryScanner.loadPoints() }.value
        var clustered = StayClusterer.cluster(photos: points)
        let limit = min(clustered.count, 40)
        for index in 0..<limit where clustered[index].placeName.isEmpty {
            clustered[index].placeName = await PlaceGeocoder.city(
                latitude: clustered[index].latitude,
                longitude: clustered[index].longitude
            )
        }
        stays = clustered
        if home == nil {
            homeProposal = StayClusterer.proposeHome(stays: clustered)
        }
        JournalCache.save(stays: stays, home: home, context: context)
        busy = false
        status = "\(points.count) located photos became \(clustered.count) stays"
    }

    func loadExample(context: ModelContext) async {
        var clustered = StayClusterer.cluster(photos: StayClusterer.examplePhotos())
        for index in clustered.indices {
            clustered[index].placeName = clustered[index].longitude > -20 ? "Lisbon" : "San Francisco"
        }
        stays = clustered
        homeProposal = StayClusterer.proposeHome(stays: clustered)
        JournalCache.save(stays: stays, home: home, context: context)
        if let proposal = homeProposal {
            let city = await PlaceGeocoder.city(latitude: proposal.latitude, longitude: proposal.longitude)
            status = city.isEmpty ? "Loaded the example library" : "Example library. Proposed home is near \(city)."
        } else {
            status = "Loaded the example library"
        }
    }

    func confirmHome(_ home: HomeConfirmation, context: ModelContext) async {
        self.home = home
        homeProposal = nil
        JournalCache.save(stays: stays, home: home, context: context)
        guard api.token != nil else {
            status = "Home saved on this iPhone. Sign in to upload it."
            return
        }
        await run("Home saved") {
            let member = try await self.api.putHome(home)
            self.member = member
        }
    }

    func uploadStays() async {
        guard api.token != nil else {
            status = "Sign in before uploading stays"
            return
        }
        guard home != nil else {
            status = "Confirm home before uploading stays"
            return
        }
        await run("Uploaded \(stays.count) stays") {
            if let home = self.home {
                _ = try await self.api.putHome(home)
            }
            try await self.api.syncStays(self.stays)
        }
    }

    func syncGmail() async {
        guard api.token != nil else {
            status = "Sign in before syncing tickets"
            return
        }
        do {
            let url = try await api.gmailStart()
            let callback = try await WebSignIn.shared.start(url: url)
            let items = URLComponents(url: callback, resolvingAgainstBaseURL: false)?.queryItems ?? []
            let flights = items.first { $0.name == "flights" }?.value ?? "0"
            let failures = items.first { $0.name == "failures" }?.value ?? "0"
            status = "Synced tickets. \(flights) flights, \(failures) emails still need a retry."
        } catch {
            status = error.localizedDescription
        }
    }

    func buildTrips() async {
        await run("Trips updated") {
            let response = try await self.api.assemble()
            self.trips = response.trips
            self.suggestions = response.mergeSuggestions
        }
    }

    func refreshTrips() async {
        guard api.token != nil else { return }
        await run("") {
            self.trips = try await self.api.trips()
            self.suggestions = try await self.api.suggestions()
            if self.status == "Trips updated" || self.status.isEmpty {
                self.status = ""
            }
        }
    }

    func loadTrip(_ id: Int, context: ModelContext) async -> TripDetailDTO? {
        if api.token != nil {
            do {
                let detail = try await api.trip(id)
                JournalCache.save(trip: detail, context: context)
                return detail
            } catch {
                status = error.localizedDescription
            }
        }
        return JournalCache.loadTrip(id: id, context: context)
    }

    func saveDay(tripId: Int, day: String, body: String, context: ModelContext) async -> TripDetailDTO? {
        do {
            let detail = try await api.updateDay(tripId: tripId, day: day, body: body)
            JournalCache.save(trip: detail, context: context)
            status = "Saved"
            return detail
        } catch {
            status = error.localizedDescription
            return nil
        }
    }

    func saveTitle(tripId: Int, title: String, context: ModelContext) async -> TripDetailDTO? {
        do {
            let detail = try await api.updateTitle(tripId: tripId, title: title)
            JournalCache.save(trip: detail, context: context)
            if let index = trips.firstIndex(where: { $0.id == tripId }) {
                trips[index].title = detail.title
            }
            return detail
        } catch {
            status = error.localizedDescription
            return nil
        }
    }

    func accept(_ suggestion: MergeSuggestionDTO) async {
        await run("Trips merged") {
            _ = try await self.api.acceptMerge(suggestion.id)
            self.trips = try await self.api.trips()
            self.suggestions = try await self.api.suggestions()
        }
    }

    func dismiss(_ suggestion: MergeSuggestionDTO) async {
        await run("Suggestion dismissed") {
            try await self.api.dismissMerge(suggestion.id)
            self.suggestions = try await self.api.suggestions()
        }
    }

    func joinHousehold(code: String) async {
        await run("Joined household") {
            self.member = try await self.api.join(inviteCode: code)
        }
    }

    private func keep(_ response: AuthResponse) {
        api.token = response.sessionToken
        member = response.member
        KeychainStore.save(response.sessionToken)
        if let home = response.member.home {
            self.home = HomeConfirmation(
                city: home.city,
                latitude: home.latitude,
                longitude: home.longitude,
                radiusKm: home.radiusKm
            )
        }
    }

    private func run(_ success: String, _ work: @escaping () async throws -> Void) async {
        busy = true
        do {
            try await work()
            if !success.isEmpty { status = success }
        } catch {
            status = error.localizedDescription
        }
        busy = false
    }
}

private final class ResumeGate {
    private var resumed = false

    func resume(_ continuation: CheckedContinuation<URL, Error>, returning url: URL) {
        guard !resumed else { return }
        resumed = true
        continuation.resume(returning: url)
    }

    func resume(_ continuation: CheckedContinuation<URL, Error>, throwing error: Error) {
        guard !resumed else { return }
        resumed = true
        continuation.resume(throwing: error)
    }
}

final class WebSignIn: NSObject, ASWebAuthenticationPresentationContextProviding {
    static let shared = WebSignIn()
    private var session: ASWebAuthenticationSession?

    func start(url: URL) async throws -> URL {
        try await withCheckedThrowingContinuation { continuation in
            let gate = ResumeGate()
            let session = ASWebAuthenticationSession(url: url, callbackURLScheme: "traveljournal") { callback, error in
                if let callback {
                    gate.resume(continuation, returning: callback)
                } else {
                    gate.resume(continuation, throwing: error ?? URLError(.cancelled))
                }
            }
            session.presentationContextProvider = self
            session.prefersEphemeralWebBrowserSession = true
            self.session = session
            if !session.start() {
                gate.resume(continuation, throwing: URLError(.cancelled))
            }
        }
    }

    func presentationAnchor(for session: ASWebAuthenticationSession) -> ASPresentationAnchor {
        let scenes = UIApplication.shared.connectedScenes.compactMap { $0 as? UIWindowScene }
        return scenes.flatMap(\.windows).first { $0.isKeyWindow } ?? ASPresentationAnchor()
    }
}
