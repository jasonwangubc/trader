import { SignUp } from "@clerk/nextjs";

export const metadata = { title: "Sign up" };

export default function SignUpPage() {
  return (
    <div className="min-h-screen flex items-center justify-center bg-background px-4">
      <div className="space-y-6 text-center">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">trader</h1>
          <p className="text-muted-foreground text-sm mt-1">
            Create your account to get started
          </p>
        </div>
        <SignUp />
      </div>
    </div>
  );
}
