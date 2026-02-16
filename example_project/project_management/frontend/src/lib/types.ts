export type IdName = { id: string | number; name?: string };
export type MeasurementValue = { value: number; unit: string };

export type Customer = {
  id: string | number;
  companyName: string;
  groupName: string;
  number?: number | null;
  keyAccount?: { id: string | number; fullName?: string; username?: string } | null;
};

export type CurvePoint = {
  id: string | number;
  volumeDate: string;
  volume: number;
};

export type Volume = {
  id: string | number;
  sop: string;
  eop: string;
  description?: string | null;
  usedVolume?: boolean;
  isVolumeInVehicles?: boolean;
  projectPhaseType?: IdName | null;
  customervolumecurvepointList?: { items: CurvePoint[] };
};

export type Derivative = {
  id: string | number;
  name: string;
  derivativeType?: IdName | null;
  Plant?: IdName | null;
  piecesPerCarSet?: number | null;
  normDailyQuantity?: number | null;
  maxDailyQuantity?: number | null;
  volumeDescription?: string | null;
  customervolumeList?: { items: Volume[] };
};

export type Project = {
  id: string | number;
  name: string;
  totalVolume?: number | null;
  probabilityOfNomination?: MeasurementValue | null;
  earliestSop?: string | null;
  latestEop?: string | null;
  customerVolumeFlex?: MeasurementValue | null;
  customer?: Customer | null;
  projectPhaseType?: IdName | null;
  projectType?: IdName | null;
  currency?: { id: string | number; name?: string; abbreviation?: string } | null;
  projectteamList?: {
    items: Array<{
      id?: string | number;
      active?: boolean;
      projectUserRole?: IdName | null;
      responsibleUser?: { id: string | number; fullName?: string; username?: string } | null;
    }>;
  };
  derivativeList?: { items: Derivative[] };
};

export type CurveSeriesPoint = {
  date: string;
  total_volume?: number;
  used_volume?: number;
  value?: number;
};
