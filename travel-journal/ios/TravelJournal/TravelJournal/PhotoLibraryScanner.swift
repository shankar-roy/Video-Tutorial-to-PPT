import CoreLocation
import Foundation
import Photos
import UIKit

enum PhotoLibraryScanner {
    static func requestAccess() async -> Bool {
        let status = PHPhotoLibrary.authorizationStatus(for: .readWrite)
        if status == .authorized || status == .limited { return true }
        let updated = await PHPhotoLibrary.requestAuthorization(for: .readWrite)
        return updated == .authorized || updated == .limited
    }

    static func loadPoints() -> [PhotoPoint] {
        let options = PHFetchOptions()
        options.sortDescriptors = [NSSortDescriptor(key: "creationDate", ascending: true)]
        let assets = PHAsset.fetchAssets(with: .image, options: options)
        var points: [PhotoPoint] = []
        assets.enumerateObjects { asset, _, _ in
            guard let location = asset.location, let takenAt = asset.creationDate else { return }
            points.append(
                PhotoPoint(
                    assetId: asset.localIdentifier,
                    takenAt: takenAt,
                    latitude: location.coordinate.latitude,
                    longitude: location.coordinate.longitude
                )
            )
        }
        return points
    }

    static func thumbnail(assetId: String) -> UIImage? {
        let assets = PHAsset.fetchAssets(withLocalIdentifiers: [assetId], options: nil)
        guard let asset = assets.firstObject else { return nil }
        let options = PHImageRequestOptions()
        options.isSynchronous = true
        options.deliveryMode = .fastFormat
        options.resizeMode = .fast
        var image: UIImage?
        PHImageManager.default().requestImage(
            for: asset,
            targetSize: CGSize(width: 240, height: 240),
            contentMode: .aspectFill,
            options: options
        ) { result, _ in
            image = result
        }
        return image
    }
}

enum PlaceGeocoder {
    static func city(latitude: Double, longitude: Double) async -> String {
        let location = CLLocation(latitude: latitude, longitude: longitude)
        let geocoder = CLGeocoder()
        let marks = try? await geocoder.reverseGeocodeLocation(location)
        let place = marks?.first
        return place?.locality ?? place?.subAdministrativeArea ?? place?.administrativeArea ?? ""
    }
}
