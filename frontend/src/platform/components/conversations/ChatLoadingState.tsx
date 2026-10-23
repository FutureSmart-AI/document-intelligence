import { Loader2 } from "lucide-react";

export const ChatLoadingState = () => {
  return (
    <div className="flex items-center justify-center h-full">
      <Loader2 className="w-10 h-10 text-blue-600 animate-spin" />
    </div>
  );
};
