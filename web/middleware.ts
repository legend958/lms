import { NextResponse, type NextRequest } from "next/server";

const HOME: Record<string, string> = {
  student: "/student/dashboard",
  teacher: "/teacher/documents",
  admin: "/admin/users",
};

export function middleware(request: NextRequest) {
  const { pathname } = request.nextUrl;
  const role = request.cookies.get("lms_role")?.value;
  const home = role ? HOME[role] : undefined;

  if (pathname === "/login") {
    if (home) {
      return NextResponse.redirect(new URL(home, request.url));
    }
    return NextResponse.next();
  }

  if (!home) {
    const loginUrl = new URL("/login", request.url);
    loginUrl.searchParams.set("next", pathname);
    return NextResponse.redirect(loginUrl);
  }

  if (!pathname.startsWith(`/${role}`)) {
    return NextResponse.redirect(new URL(home, request.url));
  }

  return NextResponse.next();
}

export const config = {
  matcher: ["/((?!api|_next/static|_next/image|favicon.ico).*)"],
};
