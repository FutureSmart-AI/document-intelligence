interface FormSectionHeaderProps {
  title: string;
  description?: string;
  className?: string;
}

export const FormSectionHeader = ({
  title,
  description,
  className = "",
}: FormSectionHeaderProps) => {
  return (
    <div className={`${className}`}>
      <h4 className="text-xs font-semibold uppercase tracking-wide text-slate-600">
        {title}
      </h4>
      {description && (
        <p className="mt-0.5 text-xs text-slate-500">{description}</p>
      )}
    </div>
  );
};
