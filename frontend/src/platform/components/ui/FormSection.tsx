import React from "react";

import { FormSectionHeader } from "./FormSectionHeader";

interface FormSectionProps {
  title: string;
  description?: string;
  children: React.ReactNode;
  className?: string;
  headerClassName?: string;
}

export const FormSection = ({
  title,
  description,
  children,
  className = "",
  headerClassName = "",
}: FormSectionProps) => {
  return (
    <div className={`space-y-6 ${className}`}>
      <FormSectionHeader
        title={title}
        description={description}
        className={headerClassName}
      />
      {children}
    </div>
  );
};
