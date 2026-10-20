export const PASSWORD_MIN_LENGTH = 12;

export function passwordValidationMessage(password: string): string | null {
  if (password.length < PASSWORD_MIN_LENGTH) {
    return `Use at least ${PASSWORD_MIN_LENGTH} characters.`;
  }
  if (!/[a-z]/.test(password) || !/[A-Z]/.test(password)) {
    return "Include both uppercase and lowercase letters.";
  }
  if (!/\d/.test(password)) {
    return "Include at least one number.";
  }
  return null;
}
