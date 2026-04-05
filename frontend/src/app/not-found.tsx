import Link from "next/link";

/**
 * Next.js App Router custom 404 page.
 */
export default function NotFound() {
  return (
    <div className="flex flex-col items-center justify-center min-h-[60vh] px-4">
      <div className="bg-white rounded-lg shadow-md p-8 max-w-md text-center">
        <h2 className="text-4xl font-bold text-gray-300 mb-2">404</h2>
        <h3 className="text-xl font-bold text-gray-900 mb-2">Page not found</h3>
        <p className="text-sm text-gray-600 mb-6">
          The page you&apos;re looking for doesn&apos;t exist or has been moved.
        </p>
        <Link
          href="/dashboard"
          className="inline-block bg-blue-600 text-white rounded-md px-6 py-2 text-sm font-medium hover:bg-blue-700 transition-colors focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-2"
        >
          Back to Dashboard
        </Link>
      </div>
    </div>
  );
}
