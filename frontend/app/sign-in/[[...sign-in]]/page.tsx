import { SignIn } from "@clerk/nextjs";

export const metadata = { title: "Sign in" };

export default function SignInPage() {
  return (
    <div className="min-h-screen flex items-center justify-center bg-background px-4">
      <div className="space-y-6 text-center">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">trader</h1>
          <p className="text-muted-foreground text-sm mt-1">
            Minervini-style breakout trading discipline tool
          </p>
        </div>
        <SignIn />
      </div>
    </div>
  );
}
